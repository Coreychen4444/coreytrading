#!/usr/bin/env python3
"""pipeline.py — 定时任务的**采集/编排层**, heartbeat 与 overnight-briefing 共用。

为什么存在 (用户 : "两个应该用同一套底层逻辑, 仅保留差异化处理"):
    两个任务的脚本序列此前各自抄在 tasks/*.md 的 prose 里。同一条流程两处维护,
    已经漂出四处真实分歧:
      1. **顺序错**briefing 在 trade_history_sync / watchlist_migrate **之前**跑
                    portfolio_brief → 当天新成交不在预算计算里, 新 promote 的票不在简报里
      2. **漏步骤**briefing 从不跑 order_sync → 早简报的 pending_orders 是隔夜旧的
      3. **NAV 滞后**nav_snapshot 排在 portfolio_brief 之后 → 预算行用的是昨天的 NAV
      4. **--apply 漏传**前两个任务都漏了 watchlist_migrate --apply, 自动迁移
                    从未生效
    还有两个"监控不许无声变黑"的机制 —— OpenD-down 告警和 heartbeat dead-man 时间戳 ——
    此前是 prompt 里的 python heredoc, 靠 LLM 记得执行。现在是代码。

差异化只保留两条 (其余全共享):
    · cadence      heartbeat 触发式 / briefing 每日全量
    · 收盘依赖     kline_cache / regime_transition / iv_logger / nav_snapshot /
                  skhy_premium / entry_guard 需要上一交易日的完整收盘 → 不进 heartbeat
                  (briefing / postclose 各按需; entry 两者都跑)

盘中运行的两道闸 (ET 13:56 事故):
    整个收盘依赖层都把 kline 的 rows[-1] 读作"昨收", 而此前唯一的保护是
    "定时任务恰好在 ET 盘前跑" —— 一个时间巧合, 不是机制。手工在盘中跑一次,
    kline_cache 就把当日进行中的 bar (close = 拉数据那一刻的现价) 落了库,
    regime_transition 拿它当收盘, **永久**把 NBIS 卡片降了一档并写进 regime_history。
      闸 1 (源头)  kline_cache 盘中丢弃当日未完成 bar → 缓存只含已完成日线,
                  所有下游 (含手工跑 exit_guard / entry_guard / level_map) 自动正确
      闸 2 (本层)  eod_only 的三个账本 (iv/nav/skhy) 拉的是**实时**读数、每交易日
                  只写一行 → 盘中跳过, 否则把某个盘中时刻当成"这一天的记录"

CLI:
    .venv/moomoo/bin/python3 quant/ops/pipeline.py --mode heartbeat [--json]
    .venv/moomoo/bin/python3 quant/ops/pipeline.py --mode briefing [--event-day] [--json]
    .venv/moomoo/bin/python3 quant/ops/pipeline.py --mode briefing --dry-run   # 只印计划
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from quant.core.io_json import atomic_write_json  # noqa: E402
from quant.core.paths import HEARTBEAT_STATE_PATH, ROOT  # noqa: E402
from quant.core.timeutil import et_session_state  # noqa: E402
from quant.core.market_sessions import scheduled_session_due

PY = sys.executable                       # 用当前解释器 (应为 .venv/moomoo/bin/python3)
STATE_PATH = HEARTBEAT_STATE_PATH
OPEND_DOWN_KEY = "INFRA:opend_down"
OPEND_ALERT_COOLDOWN_S = 12 * 3600
STAGE_TIMEOUT_S = 300

HEARTBEAT, BRIEFING, POSTCLOSE = "heartbeat", "briefing", "postclose"
BOTH = (HEARTBEAT, BRIEFING)
ALL_MODES = (HEARTBEAT, BRIEFING, POSTCLOSE)
# The close-dependent modes evaluate exits against the pre-transition card before
# applying a regime transition. Otherwise a demotion can replace the breached stop
# before the exit stack has observed it. External effectors run only after the
# report has emitted any alerts.


class Stage:
    """一个采集步骤。fatal=True 时失败即中止 (后续步骤会读到坏数据)。"""

    def __init__(self, key: str, argv: List[str], modes, desc: str, *,
                 fatal: bool = False, parse_json: bool = False, needs_opend: bool = True,
                 eod_only: bool = False, timeout_s: int | None = None):
        self.key, self.argv, self.modes, self.desc = key, argv, tuple(modes), desc
        self.fatal, self.parse_json, self.needs_opend = fatal, parse_json, needs_opend
        # eod_only: 拉**实时**读数、每交易日只写一行的账本 (IV / NAV / ADR 溢价)。
        # 盘中跑 = 把某个盘中时刻的读数当成"这一天的记录", 而 IV percentile 之类
        # 靠的是同一时点的可比序列。日线类不用标 —— kline_cache 自己会丢当日未完成 bar。
        self.eod_only = eod_only
        self.timeout_s = timeout_s


# ── 唯一的正确顺序 ────────────────────────────────────────────────
#
# 依赖链: broker 真值 → 事件/订单落库 → watchlist 对账与迁移 → 收盘数据层
#        → NAV(预算分母) → portfolio_brief(消费以上全部) → regime(转移)
#        → entry_guard(消费 brief+NAV+转移后授权, 落 option_plans.json)
#        → 效果层 reminders / today_group(纯读 option_plans.json → 必须排在 entry 之后)
# portfolio_brief 必须在**全部采集步之后**跑 —— 排在采集中间就是在读半成品。
# Exit evaluation runs before regime transitions because a demotion may replace the
# breached stop. Reminder and group effectors run after both the brief and transition.
STAGES: List[Stage] = [
    Stage("account", [".claude/skills/openapi/scripts/trade/get_portfolio.py",
                      "--currency", "USD", "--json"], (BRIEFING,),
          "账户资金/购买力真值 (必须带 --currency USD, 否则服务端按 HKD 换算)",
          parse_json=True),
    Stage("positions", ["quant/sync/stock_fetch.py"], ALL_MODES,
          "刷新 positions.json broker block (agent/lots/pending_orders 保留)。"
          "postclose 也刷新，避免收盘异动与退出检查读取盘前报价",
          fatal=True),
    Stage("orders", ["quant/sync/order_sync.py", "--json"], BOTH,
          "在途订单 → pending_orders (briefing 此前完全没跑这步)。完成即推 📋 在途挂单 "
          "(防忘单开盘成交: 含无卡 orphan, 每轮都推, 无挂单不推)",
          parse_json=True),
    Stage("fills", ["quant/sync/trade_history_sync.py", "--json"], BOTH,
          "新成交 → events/<YYYY-MM>/trades.jsonl (dedup by order_id)", parse_json=True),
    Stage("reconcile", ["quant/sync/close_reconcile.py", "--json"], BOTH,
          "平仓收尾: qty 归零的卡片清 lots + 标 closed + 落 exit_info (lots 归档不删)。"
          "须在 fills 之后 (要事件流算 realized_pnl)、brief 之前 (否则简报拿死卡当持仓)",
          parse_json=True, needs_opend=False),
    Stage("exit_score", ["quant/journal/exit_score.py", "--json"], BOTH,
          "结构性退出自动评分 → scores.jsonl (2026-08-25 用户, NBIS 案): 降级在案 + "
          "close_reconcile 落了 exit_info → 时延档 D+0=A/D+1=B/D+2+=C, 幂等去重。"
          "须在 reconcile 之后 (exit_info 是判据)、watchlist_sync 之前 (促升覆盖 "
          "closed 卡前评完); unresolved_demotions (点名) 的对偶 (记分)",
          parse_json=True, needs_opend=False),
    Stage("watchlist_sync", ["quant/sync/watchlist_sync.py", "--json"], BOTH,
          "moomoo 自选分组 ↔ watchlist.json 对账 (briefing 须先于 kline_cache, 新 stub "
          "当轮即可拉 K 线)。已持仓 → 自动移出分组 (入场候选语义) / 平仓 → "
          "自动加回分组 (每次平仓一次并盖章) + 平仓卡 agent 块原样搬回候选卡 ("
          "待过期重画; 已有 zones 的卡不覆盖) — 进 heartbeat "
          "让成交后 ≤6h 清理, 须在 positions/fills 之后 (broker 真值当轮新)、**reconcile "
          "之后**(加回判据是 close_reconcile 落的 exit_info.closed_at, 排在它之前 = "
          "平仓当轮不回组, 白等一整轮)",
          parse_json=True),
    Stage("watchlist_migrate", ["quant/sync/watchlist_migrate.py", "--apply", "--json"], BOTH,
          "watchlist→positions 迁移并执行 (--apply 缺失 = 只检测不写)。须在 lots 之前，"
          "确保重进首笔成交的 lot 从新候选卡派生计划字段，而不是读取旧平仓卡",
          parse_json=True),
    Stage("lots", ["quant/sync/lot_reconcile.py", "--json"], BOTH,
          "买入落账: 未入账的 BUY fill → lots.position + position_summary (tier 恒 "
          "position, 计划字段派生自卡片 zones)。须在 fills 与 watchlist_migrate 之后、"
          "brief 之前。部分卖出/数量对不上一律不动手只报",
          parse_json=True, needs_opend=False),
    Stage("autopaint", ["quant/ops/card_autopaint.py", "--json"], BOTH,
          "Detect eligible cards without zones and start detached card-building agents. "
          "Runs after watchlist reconciliation so new stubs are visible immediately; "
          "returns without waiting, so completed cards take effect on a later pipeline run.",
          parse_json=True, needs_opend=False),
    Stage("klines", ["quant/data/kline_cache.py"], (BRIEFING, POSTCLOSE),
          "日线缓存增量 — 收盘依赖; exit/entry guard 的结构标签靠它"),
    Stage("nav", ["quant/data/nav_snapshot.py", "--json"], (BRIEFING, POSTCLOSE),
          "每日 NAV + 基准快照 — **必须先于 reminders/portfolio_brief**(预算行与 "
          "moomoo z1 备注金额的分母)。postclose 收盘后即刷当日 NAV, "
          "转移后重画的备注金额用当日收盘账户; 此前只在盘前跑 = cap 数学落后一个交易日, "
          "且 briefing 里 reminders 排在 nav 前 = 画线用旧 NAV、brief 用新 NAV 两处打架",
          parse_json=True, eod_only=True),
    Stage("cashflows", ["quant/data/cash_flow_sync.py", "--json"], (BRIEFING,),
          "券商资金流水 → events/cash_flows.jsonl: 只收银证转账 (出入金 = 外部流), 回看 7 日按 "
          "clearing_date 逐日查, key 去重幂等。出入金不是盈亏 —— 周复盘/绩效的 NAV 变动必须剔它 "
          "(口径 core/nav, 出口 report/nav_report)。"
          "不 eod_only: 按清算日查, 盘中跑不会把盘中时刻当成一天; 只进 briefing = 每日一次够用",
          parse_json=True),
    Stage("iv", ["quant/data/iv_logger.py"], (BRIEFING,), "每日 ATM IV 快照 (幂等)",
          eod_only=True, timeout_s=900),   # 链接口限频 10 次/30 秒, 每票 2 条链 (30D + LEAPS) × 3.1s
    Stage("earnings", ["quant/data/earnings_calendar.py", "--json"], (BRIEFING,),
          "前视财报日历 + per-ticker 缩仓比例 → market/earnings_calendar.json "
          "(earnings_window 路 A: 反应日 T+0 仓位上限 × 缩仓比例 × 0.7)。**须先于 "
          "brief/entry**— 它俩读缓存不联网; 日历缺失 = 那天不缩仓, 而 40% 名义上限"
          "在财报日等于把整个组合预算押在一次财报上", parse_json=True),
    Stage("skhy", ["quant/data/skhy_premium.py"], (BRIEFING,), "SKHY ADR 溢价 gate (幂等)",
          eod_only=True),
    Stage("drift", ["quant/decision/structure_drift.py", "--log", "--json"], (BRIEFING,),
          "结构漂移观测 (四维: 离锚/区间外/gate 可达/波动率 — ②POC 已摘除) — **只观测不裁决**, "
          "阈值未校准, 产出 advisory 待重画清单", parse_json=True, needs_opend=False),
    # ↓ 消费层: 以上全部落库完成后才跑
    Stage("brief", ["quant/report/portfolio_brief.py", "--json"], ALL_MODES,
          "position_cards / fired_cards / exit_guards / zone signals / alerts; "
          "runs before regime transitions and external effectors",
          fatal=True, parse_json=True, needs_opend=False),
    Stage("regime", ["quant/decision/regime_transition.py", "--apply", "--json"],
          (BRIEFING, POSTCLOSE),
          "gate 触发即机械转移卡片 regime — 收盘依赖; 须在 brief 之后 (退出侧先于"
          "转移 2026-08-20)、效果层之前 (画线/白名单渲染转移后世界)", parse_json=True),
    Stage("option_stops", ["quant/sync/option_stop_sync.py", "--json"],
          (BRIEFING, POSTCLOSE),
          "期权卡 stop 跟随正股授予 cap 的 gate 上移, 只升不降 (S5g 已读新 gate 时卡上不能还"
          "躺着旧线)。须在 regime 之后 (新 up 转移当轮生效)、reminders 之前 (画线与卡同一个数)",
          parse_json=True, needs_opend=False),
    Stage("agents", ["quant/ops/agent_jobs.py", "run", "--json", "--apply"],
          (BRIEFING, POSTCLOSE),
          "领域工作流和受控复核写卡；退出评估与机械转移之后、提醒线和入场计划之前。",
          parse_json=True, needs_opend=False, timeout_s=10800),
    # ↓ 入场计划: 效果层的**上游**。reminders (抄底首档线 / oz1) 与 today_group (抄底正股 + 合约进组)
    #   都纯读 entry --log 落的 option_plans.json —— 排在它们之后 = 同一轮画的是上一份计划。
    #   抄底首档按规则随收盘下移 (reversal.ladder: 首档 = min(信号收盘, 最新收盘)); 若 entry 只进
    #   briefing 且排在效果层之后, 收盘到线刷新隔 ~20h、跨进下一个交易日盘中, 券商侧线还挂着旧锚;
    #   同一条滞后也让收破止跌低 (信号作废) 的计划在 Today 组与授权盖章里多留一整天。
    Stage("entry", ["quant/decision/entry_guard.py", "--log"], (BRIEFING, POSTCLOSE),
          "入场执行计划 (PLAN 变动才落 decisions.jsonl) + option_plans.json (抄底 / 期权选约的授权与首档)。"
          "须在 brief / nav / regime / agents 之后 (消费转移后世界与写卡后状态)、reminders / today_group "
          "之前 (它们纯读 option_plans.json)。postclose 同跑 = 当日收盘即重锚首档、撤掉作废信号; "
          "briefing 的输出为**渲染文本**, 原样进简报",
          needs_opend=False),
    # ↓ 效果层: 只写外部副作用 (moomoo 画线/分组), brief 不消费它们的任何输出。
    #    定序: 告警先出门 —— 画线/白名单的 OpenD 调用一旦变慢或
    #   挂住 (STAGE_TIMEOUT 上限), 排在 brief 之前就是在堵告警; 排在之后, 告警
    #   已在 TG 上。附带收益: reminders 读的 exit_guards.json 从"上一轮 brief
    #   (≤6h 前)"变成**同轮刚算的** ——  裁决接受的 ≤6h 画线滞后消除
    #   (postclose  起同享: 此前它画 trail 用的是 ≤10h 前旧收盘算的值)。
    #   已知代价: brief (fatal) 失败的那一轮画线/白名单不刷, 停留 ≤6h 到下一轮
    #   —— brief 失败是响的 (fatal + 任务层可见), 可接受。
    Stage("reminders", ["quant/sync/price_reminder_sync.py", "--json"], ALL_MODES,
          "reconcile owned broker reminders from the post-transition card and the "
          "same-run exit stack; manual reminders remain untouched",
          parse_json=True),
    Stage("today_group", ["quant/sync/today_group_sync.py", "--json"], ALL_MODES,
          "reconcile the optional entry-authorization group from post-transition state",
          parse_json=True),
]

# Cache-only preview is an allowlist: new collectors/effectors are skipped until
# their complete call graph has an explicitly read-only contract. In particular,
# entry_guard without --log can still fetch an option chain through option_plan.
READ_ONLY_STAGE_ARGS = {"brief": ["--read-only"]}


# ── OpenD 探活 ────────────────────────────────────────────────────

def opend_endpoint():
    return (os.environ.get("FUTU_OPEND_HOST") or "127.0.0.1",
            int(os.environ.get("FUTU_OPEND_PORT") or 11111))


def opend_alive(timeout: float = 3.0) -> bool:
    host, port = opend_endpoint()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ── 状态文件 (与 portfolio_brief 共用同一份, 告警冷却全局去重) ──────
#
#  用户 定: heartbeat 和 briefing **都可以发告警**, 不是 heartbeat 专属。
# 共用冷却 = 谁先看到谁发, 另一个不重复。

def load_state() -> Dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(st: Dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_PATH, st)


def notify(msg: str) -> bool:
    try:
        p = subprocess.run([PY, str(ROOT / "quant/ops/tg_notify.py"), msg],
                           cwd=ROOT, capture_output=True, timeout=30)
        return p.returncode == 0
    except Exception:
        return False


def handle_opend_down(send: bool) -> Dict:
    """12h 冷却的 INFRA 告警。监控变黑必须可见 —— 此前是 prompt 里的 heredoc。"""
    st = load_state()
    alerts = st.setdefault("alerts", {})
    prev = alerts.get(OPEND_DOWN_KEY) or {}
    now = int(time.time())
    fired = False
    if now - int(prev.get("lastAlertTs") or 0) > OPEND_ALERT_COOLDOWN_S:
        host, port = opend_endpoint()
        if send:
            notify(f"⚠️ OpenD unreachable ({host}:{port}) — 持仓监控已变黑。"
                   f"先 tmux attach -t opend 看是否卡在登录提示；"
                   f"进程已退出则跑 opend/start.sh")
        alerts[OPEND_DOWN_KEY] = {"active": True, "lastAlertTs": now}
        save_state(st)
        fired = True
    elif not prev.get("active"):
        alerts[OPEND_DOWN_KEY] = {**prev, "active": True}
        save_state(st)
    return {"alert_fired": fired, "cooldown_s": OPEND_ALERT_COOLDOWN_S}


def handle_opend_recovered(send: bool) -> bool:
    st = load_state()
    alerts = st.setdefault("alerts", {})
    if (alerts.get(OPEND_DOWN_KEY) or {}).get("active"):
        if send:
            notify("✅ OpenD 恢复，监控正常")
        alerts[OPEND_DOWN_KEY]["active"] = False
        save_state(st)
        return True
    return False


def push_brief_alerts(data: Dict):
    """Push the brief's alert payload before external effectors.

    内容 = fired_cards 原文 (代码渲染的持仓状态卡) + alerts 行 —— 与 heartbeat.md
    输出规则同一份合同, 只是发射点从"任务 agent 在整条 pipeline 之后"提前到
    "brief 刚算完"。没有告警 = 不推 (HEARTBEAT_OK 的安静保持)。推送失败返回
    False, 任务层 agent 见 tg_pushed=False 按旧规则兜底推送。
    """
    alerts = data.get("alerts") or []
    cards = data.get("fired_cards") or []
    if not alerts and not cards:
        return "no_alerts"          # 与"推送失败"区分 — False 才需要 agent 兜底
    #  tg_notify CLI 默认富消息, 这里只给
    # 每张卡的头行加粗 (格式不是改写 —— 卡片正文一个字不动,  原文规矩不变)。
    blocks = [_bold_head(str(c)) for c in cards]
    if alerts:
        blocks.append("**— alerts —**\n" + "\n".join(str(x) for x in alerts))
    return notify("\n\n".join(blocks))


def push_open_orders(data: Dict):
    """order_sync 完成即推在途挂单 (忘掉的隔夜挂单会在开盘自动成交)。

    文本由 order_sync 用 core/open_orders 渲染好 (`message`), 这里只投递。每轮都推 ——
    忘单的解药是重复看见, 不设冷却; 无挂单 = "no_orders" 不推。
    """
    msg = data.get("message")
    if not msg:
        return "no_orders"
    return notify(msg)


def _bold_head(card: str) -> str:
    """卡片第一行 → `**…**` (富消息里成粗体头; 老路回落时 `*…*` 同样是粗体)。"""
    first, sep, rest = card.partition("\n")
    if not first.strip() or first.startswith("**"):
        return card
    return f"**{first.strip()}**{sep}{rest}"


def format_agent_health(health: Dict) -> str:
    """Summarize current issues separately from historical recovery events."""
    pending = health.get("pending_events", [])
    issues = health.get("issues", [])
    recovered = [e for e in pending if e.get("kind") == "recovered"]
    lines = ["⚠️ 领域 Agent 待处理" if issues else "✅ 领域 Agent 状态更新"]
    if issues:
        groups = {}
        for issue in issues:
            target = issue.get("ticker") or issue.get("job_kind") or issue.get("role") or issue.get("source", "unknown")
            key = (target, issue.get("code", "unknown"))
            groups[key] = groups.get(key, 0) + 1
        lines.append(f"当前未解决：{len(issues)} 项")
        for (target, code), count in sorted(groups.items()):
            lines.append(f"- {target}：{code}" + (f"（{count} 条记录）" if count > 1 else ""))
    else:
        lines.append("当前无未解决告警。")
    if recovered:
        tickers = sorted({e["issue"]["ticker"] for e in recovered if e.get("issue", {}).get("ticker")})
        lines.append(f"历史恢复：{len(recovered)} 条记录（非新增故障）" +
                     ("；涉及 " + "、".join(tickers) if tickers else ""))
    lines.append("详细事件与运行回执保留在本地 var/agent-runs/。")
    return "\n".join(lines)


def stamp_heartbeat() -> None:
    """dead-man 专属时间戳。**只有 heartbeat 模式写**, briefing 不能碰 ——
    briefing 每天也跑 portfolio_brief, 用 lastChecks.portfolio_brief 判死活会
    掩盖 heartbeat 停摆。"""
    st = load_state()
    st.setdefault("lastChecks", {})["heartbeat"] = int(time.time())
    save_state(st)


# ── 执行 ──────────────────────────────────────────────────────────

def run_stage(s: Stage, extra: Optional[List[str]] = None,
              drop: Optional[List[str]] = None) -> Dict:
    flags = [f for f in s.argv[1:] if f not in (drop or [])]
    argv = [PY, str(ROOT / s.argv[0])] + flags + list(extra or [])
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           timeout=s.timeout_s or STAGE_TIMEOUT_S)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", f"timeout >{s.timeout_s or STAGE_TIMEOUT_S}s"
    except Exception as e:                                    # noqa: BLE001
        rc, out, err = -1, "", f"{type(e).__name__}: {e}"
    res = {"key": s.key, "ok": rc == 0, "rc": rc, "secs": round(time.time() - t0, 1),
           "stdout": out, "stderr": err[-2000:] if err else ""}
    if s.parse_json and out.strip():
        try:
            res["data"] = json.loads(out)
        except json.JSONDecodeError:
            res["json_parse_failed"] = True
    return res


def classify_agents_stage(r: Dict) -> Dict:
    """agents 阶段的退出码 1 盖住三种回执: failed / partial / needs_review (agent_jobs 语义)。

    partial = 领域任务**已结束**, 但有缺失角色、未落卡或超出本轮 repaint_bound 的待办 —— 这不是
    "这一步挂了" (简报按 reports / cards / pending_cards 逐项列待办), 所以不进 `failed`,
    改记 `partial`。failed / 回执解析不了 仍是失败。待办数据仍在 r["data"] 里原样保留。
    """
    data = r.get("data")
    if r["ok"] or not isinstance(data, dict) or data.get("status") != "partial":
        return r
    reports = data.get("reports") if isinstance(data.get("reports"), dict) else {}
    cards = data.get("cards") if isinstance(data.get("cards"), list) else []
    r["ok"] = True
    r["partial"] = {
        "failed_roles": sorted(role for role, rep in reports.items()
                               if not isinstance(rep, dict) or rep.get("status") != "succeeded"),
        "cards_not_applied": [str(c.get("ticker")) for c in cards
                              if isinstance(c, dict) and c.get("status") != "applied"],
        "pending_cards": len(data.get("pending_cards") or []),
    }
    r["note"] = "领域任务部分完成 (partial): 缺失角色 / 未落卡 / 待办按回执列出, 不是该步挂了"
    return r


def plan(mode: str) -> List[Stage]:
    return [s for s in STAGES if mode in s.modes]


def main(argv: List[str]) -> int:
    from quant.core.interpreter import require_project_interpreter
    require_project_interpreter()  # : system python killed three card jobs with a calendar error
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=[HEARTBEAT, BRIEFING, POSTCLOSE])
    ap.add_argument("--event-day", action="store_true",
                    help="宏观/财报事件日标记 → entry_guard 仅 [FYI] 播报 (不降级)")
    ap.add_argument("--json", action="store_true", help="结构化输出 (定时任务用)")
    ap.add_argument("--dry-run", action="store_true", help="只印执行计划, 不跑")
    ap.add_argument("--no-notify", action="store_true",
                    help="不发 INFRA Telegram (人工调试用)")
    ap.add_argument("--read-only", action="store_true",
                    help="缓存预览: 仅运行明确只读的报告，其余采集、状态写入、后台 agent、"
                         "外部同步全部跳过；不连接 OpenD、不发通知、不写冷却或 dead-man。"
                         "**定时任务绝不能带这个** —— 带了等于监控没跑")
    a = ap.parse_args(argv)

    stages = plan(a.mode)
    if a.dry_run:
        print(f"pipeline --mode {a.mode} 执行计划 ({len(stages)} 步):")
        for i, s in enumerate(stages, 1):
            print(f"  {i:>2}. {s.key:<18} {'[fatal]' if s.fatal else '       '} "
                  f"{' '.join(s.argv)}\n      {s.desc}")
        return 0

    send = not a.no_notify and not a.read_only
    result: Dict = {"mode": a.mode, "opend": None, "stages": {}, "aborted": None,
                    "read_only": a.read_only}

    if not a.read_only:
        due = scheduled_session_due(a.mode, datetime.now(timezone.utc))
        result["calendar"] = due
        if not due["due"]:
            bad_calendar = due["reason"].startswith("calendar_unavailable")
            result.update(status="failed" if bad_calendar else "skipped", failed=[])
            if bad_calendar and send:
                notify("⚠️ 交易日历不可用，本轮任务未执行：" + due["reason"])
            print(json.dumps(result, ensure_ascii=False))
            return 2 if bad_calendar else 0

    if not a.read_only and not opend_alive():
        result["opend"] = "down"
        result["infra_alert"] = ({"skipped": "read_only"} if a.read_only
                                 else handle_opend_down(send))
        result["aborted"] = "opend_unreachable"
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            host, port = opend_endpoint()
            print(f"{a.mode.upper()}_ERROR: OpenD unreachable ({host}:{port}) — 已按 12h 冷却处理告警")
        return 2
    session = et_session_state()
    result["et_session"] = session
    result["opend"] = "not_checked_read_only" if a.read_only else "up"
    if a.read_only:
        result["data_source"] = "existing_cache"
        result["note"] = "只读缓存预览；报价和账户未刷新，不生成新的当日入场计划。"
    result["opend_recovered"] = False if a.read_only else handle_opend_recovered(send)

    if a.mode == POSTCLOSE and session == "open" and not a.read_only:
        # 盘中自检: postclose 只能在收盘后跑。
        # due 门 (scheduled_session_due) 与这里的时段判断读同一份 XNYS 日历, 半日市与
        # DST 都由日历裁定, 不再靠手改 cron —— 走到这里 = 两个时钟不一致或手动误跑。
        # kline 守卫只会让本轮静默 no-op —— 静默的 no-op 就是"这一步不存在", 必须变成
        # 会响的错误。无冷却, 每次响。
        msg = ("⚠️ postclose 在美股常规时段内被触发 — 日历 due 门与时段判断不一致, 或手动误跑。"
               "多槽 cron 由 `agent_jobs.py due --kind postclose` 放行, 不要手改 cron; "
               "查 `quant/core/market_sessions.py` 日历与本机时钟。"
               "本轮已中止, 今晚转移由 18:30 briefing 兜底。")
        result["aborted"] = "postclose_in_session"
        result["in_session_alert"] = notify(msg) if send else "skipped"
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("POSTCLOSE_ERROR: " + msg)
        return 3

    for s in stages:
        if a.read_only and s.key not in READ_ONLY_STAGE_ARGS:
            result["stages"][s.key] = {
                "key": s.key, "ok": True, "skipped": "read_only",
                "note": "缓存预览跳过：此步骤未获只读执行资格"}
            continue
        if s.eod_only and session == "open":
            result["stages"][s.key] = {
                "key": s.key, "ok": True, "skipped": "et_session_open",
                "note": "常规时段进行中 — 每日一行的实时读数账本不在盘中写 "
                        "(会把盘中时刻当成这一天的记录)"}
            continue
        extra: List[str] = []
        if s.key == "entry" and a.event_day:
            extra.append("--event-day")
        if s.key == "agents":
            extra.extend(["--kind", a.mode])
        if a.read_only:
            extra.extend(READ_ONLY_STAGE_ARGS[s.key])
        r = run_stage(s, extra)
        if s.key == "agents":
            r = classify_agents_stage(r)
        result["stages"][s.key] = r
        if not r["ok"] and s.fatal:
            result["aborted"] = f"fatal_stage:{s.key}"
            break
        # Deliver canonical alert text before reminder/group effectors. The receipt
        # lets task wrappers avoid duplicates; read-only and --no-notify never send.
        if s.key == "brief" and r["ok"] and send:
            result["tg_pushed"] = push_brief_alerts(r.get("data") or {})
        if s.key == "orders" and r["ok"] and send:
            result["orders_pushed"] = push_open_orders(r.get("data") or {})

    ok = result["aborted"] is None
    result["failed"] = [k for k, v in result["stages"].items() if not v["ok"]]
    result["partial"] = {k: v["partial"] for k, v in result["stages"].items() if v.get("partial")}
    if not a.read_only:
        # Health has its own durable outbox; failed delivery is retried next run.
        from quant.ops.agent_health import acknowledge_events, update_health
        from quant.core.paths import AGENT_RUNS_DIR
        health = update_health(AGENT_RUNS_DIR)
        result["agent_health"] = health
        pending = health.get("pending_events", [])
        if pending and send:
            if notify(format_agent_health(health)):
                acknowledge_events(AGENT_RUNS_DIR, [e["event_id"] for e in pending])
    # dead-man 只在 heartbeat 模式、且没有 fatal 中止时落点
    if a.mode == HEARTBEAT and ok and not a.read_only:
        stamp_heartbeat()
        result["heartbeat_stamped"] = True

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if a.read_only:
            print(result["note"])
        for s in stages:
            r = result["stages"].get(s.key)
            if not r:
                print(f"  ⏭  {s.key} (未执行)")
                continue
            if r.get("skipped"):
                print(f"  ⏭  {s.key:<18}   skip — {r['note']}")
                continue
            print(f"  {'✅' if r['ok'] else '❌'} {s.key:<18} {r['secs']:>5.1f}s"
                  + ("" if r["ok"] else f"  rc={r['rc']} {r['stderr'].splitlines()[-1:] }"))
        if result["partial"]:
            print(f"\n⚠️ 部分完成: {', '.join(result['partial'])} — 待办按回执列出, 不算挂")
        if result["aborted"]:
            print(f"\n⛔ 中止: {result['aborted']}")
        elif result["failed"]:
            print(f"\n⚠️ 非致命失败: {', '.join(result['failed'])} — 其余步骤已完成")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
