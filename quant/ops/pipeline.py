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
                  skhy_premium / entry_guard 需要上一交易日的完整收盘 → 只进 briefing

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
from typing import Dict, List, Optional

from quant.core.io_json import atomic_write_json  # noqa: E402
from quant.core.paths import HEARTBEAT_STATE_PATH, ROOT  # noqa: E402
from quant.core.timeutil import et_session_state  # noqa: E402

PY = sys.executable                       # 用当前解释器 (应为 .venv/moomoo/bin/python3)
STATE_PATH = HEARTBEAT_STATE_PATH
OPEND_DOWN_KEY = "INFRA:opend_down"
OPEND_ALERT_COOLDOWN_S = 12 * 3600
STAGE_TIMEOUT_S = 300

HEARTBEAT, BRIEFING, POSTCLOSE = "heartbeat", "briefing", "postclose"
BOTH = (HEARTBEAT, BRIEFING)
# postclose (用户 指令): 盘后+夜盘允许交易 → gate ×2 在收盘一成立就要
# 转移/重画/同步提醒线, 不等次日 18:30 briefing。只跑收盘依赖链 klines→regime→
# reminders; 说明渲染与重画是任务 prompt 里的 agent 工作。briefing 的 regime 步
# 保留 = 幂等兜底 (postclose 漏跑一轮, 18:30 补上)。


class Stage:
    """一个采集步骤。fatal=True 时失败即中止 (后续步骤会读到坏数据)。"""

    def __init__(self, key: str, argv: List[str], modes, desc: str, *,
                 fatal: bool = False, parse_json: bool = False, needs_opend: bool = True,
                 eod_only: bool = False):
        self.key, self.argv, self.modes, self.desc = key, argv, tuple(modes), desc
        self.fatal, self.parse_json, self.needs_opend = fatal, parse_json, needs_opend
        # eod_only: 拉**实时**读数、每交易日只写一行的账本 (IV / NAV / ADR 溢价)。
        # 盘中跑 = 把某个盘中时刻的读数当成"这一天的记录", 而 IV percentile 之类
        # 靠的是同一时点的可比序列。日线类不用标 —— kline_cache 自己会丢当日未完成 bar。
        self.eod_only = eod_only


# ── 唯一的正确顺序 ────────────────────────────────────────────────
#
# 依赖链: broker 真值 → 事件/订单落库 → watchlist 对账与迁移 → 收盘数据层
#        → NAV(预算分母) → portfolio_brief(消费以上全部) → entry_guard(消费 brief+NAV)
# portfolio_brief 必须**最后**跑 —— 它是唯一的消费者, 排在采集中间就是在读半成品。
STAGES: List[Stage] = [
    Stage("account", [".claude/skills/openapi/scripts/trade/get_portfolio.py",
                      "--currency", "USD", "--json"], (BRIEFING,),
          "账户资金/购买力真值 (必须带 --currency USD, 否则服务端按 HKD 换算)",
          parse_json=True),
    Stage("positions", ["quant/sync/stock_fetch.py"], BOTH,
          "刷新 positions.json broker block (agent/lots/pending_orders 保留)", fatal=True),
    Stage("orders", ["quant/sync/order_sync.py", "--json"], BOTH,
          "在途订单 → pending_orders (briefing 此前完全没跑这步)", parse_json=True),
    Stage("fills", ["quant/sync/trade_history_sync.py", "--json"], BOTH,
          "新成交 → events/<YYYY-MM>/trades.jsonl (dedup by order_id)", parse_json=True),
    Stage("reconcile", ["quant/sync/close_reconcile.py", "--json"], BOTH,
          "平仓收尾: qty 归零的卡片清 lots + 标 closed + 落 exit_info (lots 归档不删)。"
          "须在 fills 之后 (要事件流算 realized_pnl)、brief 之前 (否则简报拿死卡当持仓)",
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
    Stage("regime", ["quant/decision/regime_transition.py", "--apply", "--json"],
          (BRIEFING, POSTCLOSE),
          "gate 触发即机械转移卡片 regime — 收盘依赖", parse_json=True),
    Stage("nav", ["quant/data/nav_snapshot.py", "--json"], (BRIEFING, POSTCLOSE),
          "每日 NAV + 基准快照 — **必须先于 reminders/portfolio_brief**(预算行与 "
          "moomoo z1 备注金额的分母)。postclose 收盘后即刷当日 NAV, "
          "转移后重画的备注金额用当日收盘账户; 此前只在盘前跑 = cap 数学落后一个交易日, "
          "且 briefing 里 reminders 排在 nav 前 = 画线用旧 NAV、brief 用新 NAV 两处打架",
          parse_json=True, eod_only=True),
    Stage("reminders", ["quant/sync/price_reminder_sync.py", "--json"],
          (HEARTBEAT, BRIEFING, POSTCLOSE),
          "卡片+退出栈画线 → moomoo 服务端价格提醒对账 (下方线 = max(stop,棘轮,trail) "
          "note stop:card|lock|trail, 真源 exit_guards.json; trim/gate/z1/g01; 词表所有权, "
          "手动条目永不触碰; 6h 巡逻盲区的即时性由券商 push 补) — briefing 须在 regime 之后",
          parse_json=True),
    Stage("today_group", ["quant/sync/today_group_sync.py", "--json"],
          (HEARTBEAT, BRIEFING, POSTCLOSE),
          "入场授权白名单 (gate 达标 + regime cap 显式>0, 卡片态直读) → moomoo 'Today' "
          "分组对账。整组归 agent (收藏放 Watchlist); 与 reminders 同拍 — 须在 regime "
          "之后, 转移/补画解锁当轮或 ≤6h 接上",
          parse_json=True),
    Stage("iv", ["quant/data/iv_logger.py"], (BRIEFING,), "每日 ATM IV 快照 (幂等)",
          eod_only=True),
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
    Stage("brief", ["quant/report/portfolio_brief.py", "--json"], BOTH,
          "position_cards / fired_cards / exit_guards / zone 信号 / alerts",
          fatal=True, parse_json=True, needs_opend=False),
    Stage("entry", ["quant/decision/entry_guard.py", "--log"], (BRIEFING,),
          "入场执行计划 (PLAN 变动才落 decisions.jsonl); 输出为**渲染文本**, 原样进简报",
          needs_opend=False),
]


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
        subprocess.run([PY, str(ROOT / "quant/ops/tg_notify.py"), msg],
                       cwd=ROOT, capture_output=True, timeout=30)
        return True
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
                   f"重启: <install_dir>/OpenD.app/Contents/MacOS/OpenD "
                   f"--cfg_file=<install_dir>/OpenD.xml")
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
                           timeout=STAGE_TIMEOUT_S)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", f"timeout >{STAGE_TIMEOUT_S}s"
    except Exception as e:                                    # noqa: BLE001
        rc, out, err = -1, "", f"{type(e).__name__}: {e}"
    res = {"key": s.key, "ok": rc == 0, "rc": rc, "secs": round(time.time() - t0, 1),
           "stdout": out, "stderr": err[-2000:] if err else ""}
    if s.parse_json and rc == 0 and out.strip():
        try:
            res["data"] = json.loads(out)
        except json.JSONDecodeError:
            res["json_parse_failed"] = True
    return res


def plan(mode: str) -> List[Stage]:
    return [s for s in STAGES if mode in s.modes]


def main(argv: List[str]) -> int:
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
                    help="人工 / session 内预览: brief 不消耗告警冷却、entry 不落 "
                         "decisions.jsonl、不写 dead-man 与 INFRA 状态。"
                         "**定时任务绝不能带这个**—— 带了等于监控没跑")
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

    if not opend_alive():
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
    result["opend"] = "up"
    result["opend_recovered"] = False if a.read_only else handle_opend_recovered(send)

    if a.mode == POSTCLOSE and session == "open":
        # 冬令时自检 (用户 批): postclose 在盘中被触发 = DST 换季没改
        # cron (`10 4`→`10 5`)。kline 守卫只会让本轮静默 no-op —— 静默的 no-op
        # 就是"这一步不存在", 必须变成会响的错误。无冷却, 每天响到改为止。
        msg = ("⚠️ postclose 在美股盘中被触发 — 冬令时 cron 未改? 注册表 "
               "postclose-transition `10 4`→`10 5`, gate-ladder-report `20 4`→`20 5`。"
               "本轮已中止, 今晚转移由 18:30 briefing 兜底。")
        result["aborted"] = "postclose_in_session"
        result["dst_alert"] = notify(msg) if send else "skipped"
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("POSTCLOSE_ERROR: " + msg)
        return 3

    for s in stages:
        if s.eod_only and session == "open":
            result["stages"][s.key] = {
                "key": s.key, "ok": True, "skipped": "et_session_open",
                "note": "常规时段进行中 — 每日一行的实时读数账本不在盘中写 "
                        "(会把盘中时刻当成这一天的记录)"}
            continue
        extra: List[str] = []
        if s.key == "entry" and a.event_day:
            extra.append("--event-day")
        if a.read_only and s.key == "brief":
            extra.append("--read-only")          # 不消耗真 heartbeat 的告警窗口
        if a.read_only and s.key in ("reconcile", "lots", "autopaint"):
            # A preview must not mutate trading state.
            # Autopaint launches a detached writer, so preview mode only reports targets.
            extra.append("--dry-run")
        r = run_stage(s, extra, drop=["--log"] if (a.read_only and s.key == "entry") else None)
        result["stages"][s.key] = r
        if not r["ok"] and s.fatal:
            result["aborted"] = f"fatal_stage:{s.key}"
            break

    ok = result["aborted"] is None
    result["failed"] = [k for k, v in result["stages"].items() if not v["ok"]]
    # dead-man 只在 heartbeat 模式、且没有 fatal 中止时落点
    if a.mode == HEARTBEAT and ok and not a.read_only:
        stamp_heartbeat()
        result["heartbeat_stamped"] = True

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
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
        if result["aborted"]:
            print(f"\n⛔ 中止: {result['aborted']}")
        elif result["failed"]:
            print(f"\n⚠️ 非致命失败: {', '.join(result['failed'])} — 其余步骤已完成")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
