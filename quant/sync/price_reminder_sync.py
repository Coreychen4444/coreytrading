#!/usr/bin/env python3
"""price_reminder_sync.py — 卡片关键价位 → moomoo 服务端价格提醒(对账式幂等)。

为什么存在(TV 评估关闭后的替代立项 a, 用户 批):
    heartbeat 6h 巡逻之间是盲区; TradingView webhook 不接 —— 即时性用券商原生
    price reminder 补: 把卡片关键价位挂到 moomoo 服务端, 触发由 moomoo app 推手机,
    **不依赖本地进程存活**。挂进 pipeline 随 heartbeat/briefing 自动跑 ——
    手工步骤 = 不存在(NBIS 教训)。

同步什么(每票 ≤10 条可裁线 + 2 条 pin; moomoo 服务端上限 = **每票每类型 20 条**,
PRICE_UP / PRICE_DOWN 各自计数 —— 实测, 见 SERVER_MAX_PER_TYPE; 给手动留余量):
  positions(live_tickers, 判据 broker.qty≠0):
    下方线(每 underlying 一条,  用户 裁决"下方三者取 max"):
      max(卡片 stop, 棘轮地板, 趋势死亡线) → PRICE_DOWN note "stop:card|lock|trail"
      — note type = binding 来源 (🔒=lock 棘轮 / 📉=trail 死亡线); 地板/trail
      **只读 state/exit_guards.json 已算好的值**(单一真源, 不重算 — 不越价约束/
      armed 口径/HWM 全在 exit_guard); 三条全是收盘口径, 提醒 tick 触发 = 预警非执行。
      期权持仓 basis 分开: trail(underlying 基) + underlying-basis 卡 stop 挂
      **正股 code**; 棘轮地板(期权 mark 基) + option_price 基卡 stop 挂**合约 code**
      — 不同基的价格不可比, max 只在同基内取。
    zones.no_chase_above  → PRICE_UP    note "nochase" (仅正股, **两桶都画**)
      = 当前 regime 的追价上限 (用户)。上穿 = 从这儿往上不追市价
      (Rule #11), 挂着的 zone 限价单照旧。**非 pin**: 它是本 regime 内的参与边界,
      与 trim/z1 同类, 不是状态转移轴 —— 挤每票上限时照常占名额 (线本身不会
      因价格穿越消失, 见下方 orient_sides)。撞价 (gate == no_chase 正常设计 /
      no_chase == trim 下沿 / no_chase == z1 上沿 — 单调律两端都允许相等) →
      **标签并入** "z1:11%:2k/nochase"（备注上限为 40 半宽单位；
      并不进去才回退让位), 两处 dedup 同规则, 都播报。
    zones.trim_zone[0]    → PRICE_UP    note "trim"  (仅正股)
    期权合约 G-01 下一档   → PRICE_UP    note "g01"  (lot cost×2/3/4 中高于现 mark
      的最低档; 全 lot 取 min = 下一个会响的档)
  watchlist(moomoo_listed≠false, zones 存在且未过期; 已是持仓的票不重复):
    reclaim_gates[*].level → PRICE_UP   note "gate"

  两条 gate 常驻线(**两桶都画**, 正股卡,  用户 定):
    current gate  last_regime.repair_gate.level (缺 → regime_history[-1].level)
                                        → note "gate:cur"   pin
    next gate     next_regime.entry_gate.level  → note "gate:next"  pin
    **pin = 不随现价消失, 也不被每票上限裁掉**—— gate 是状态转移轴, 它是这张卡
    "我站在哪、下一步在哪"的两个端点, 不是一条会过期的观察线。实现不是豁免侧
    过滤(PRICE_UP 挂在现价下方 = 设置即响), 而是**方向随现价定**:
        gate 在现价上方 → PRICE_UP    (突破/收复在途)
        gate 在现价下方 → PRICE_DOWN  (失守 = 刚拿到的状态在丢)
    立此条前: current gate 根本没有线(代码只画 reclaim 梯与 next gate), 而 next
    gate 被现价穿过就当"错侧"丢弃 —— 实测本书 4 张卡的 gate 此刻隐形, 其中
    AAOI 133 / META 601 正处在"穿过了但没 ×2 确认"的状态, 掉回去就清零, 恰恰
    是最该有线的时刻。**"翻方向而不是消失"是全体线的默认**, pin 不再独享 —— 见 `orient_sides`。
  远端 gate (**两桶都画**, 正股卡; 目的是看得见更多的后续关键点位;
  非 pin, 优先级最低 = 挤名额时最先被裁):
    gate+2   next_regime.next_gate_preview.level (卡上, agent 判读; `stale: true` 的
             旧上行阶梯不画 —— 那个数「已失效待重画」)      → note "gate:+2"
    再往上   `core.future_gates.for_card` —— 卡片阶梯顶之上最近的 ≤FUTURE_GATES_MAX 个
             pivot 簇上沿 (≥2 触碰, 250 根, 与底部证据研究同一把尺) + 窗口最高价;
             **不住卡、零授权、每轮按 K 线缓存重算**     → note "gate:+3 簇×4 拒14" / "gate:+4 窗高"
             备注里的收据 (触碰数 / 拒绝数) 同时编码来源: 带收据 = 机械候选, 裸 "gate:+2" = 卡上的。
    这些线**不是转移轴** —— 转移机只扫 entry_gate; 它们回答「再往上哪儿有地标」, 响了不授权任何动作。
  补全 (无论是否有持仓, 画线尽可能完整 —— z1 / nochase / gate / trim / 抄底等所有线,
  并标注出极其关键的位置):
    zone2 下沿             → PRICE_DOWN note "z2"  (**两桶都画**; 收破 = Breakdown stale + 当日 BUY 冻结)
    watchlist 补 卡 stop ("stop:card") 与 trim 下沿 ("trim") —— 此前只有持仓画
    持仓补 reclaim 梯 ("gate") 与**无授权的裸 z1** —— 取代旧的「无授权持仓不画裸 z1」。
      备注照旧编码授权: 带预算 "z1:2%:450" = Today 成员, 裸 "z1" = 无授权观察线, 两桶同义。
    ⭐ 线 (`core.key_levels`, 机械, 零授权, **pin**): "⭐箱顶 测3" = 待破的箱顶 (近 20 根最高价所在的簇、
      近 60 根被从下方测试 ≥2 次); "⭐已破 MM-DD" = 近 60 根的 ⭐突破 (之后没收破 线−0.5N);
      "⭐假突破 MM-DD" = 突破过但收破过。箱带内已有卡片线 → **吸附到那条线** (同价并入, ⭐ 当 head),
      否则画在簇上沿。过期卡 / 无 zones 的卡也画 (它不来自卡片)。
    止跌低 (`core.setups.bottom_setup` ✓止跌 在场) → "止跌低 ✓MM-DD" (两桶都画; 持仓票不走抄底授权,
      线只是「底在哪」的结构事实)。
    zone1 上沿             → PRICE_DOWN note "z1"  (回踩进参与带)
      Today 白名单成员 → "z1:2%:450" 比例+起步预算 (用户 定版):
      响铃即见"这轮能下多少", 不用先开卡。**持仓票同权**(补洞):
      新转移回组的持仓成员同样画 z1+预算; 无授权持仓不画裸 z1 (cap 0 的回踩
      不是动作触发点, 响铃只会邀请未授权加仓)。裸 "z1" = watchlist 无授权观察线 —
      note 带不带预算本身就编码授权状态。binding 仍归 entry_guard, 备注只是展示。
      **数是算出来的, 不是抄卡片上那个百分比**(用户): 名义起步分数
      (天花板型 probe / 机器源 regime cap) 再过组合层的冲击预算余额 (NAV 15%
      单日冲击上限) 与 gross 余额 (110% NAV), 三者取小 = `grant_actual_cap`
      → `core.sizing.available_cap`, 与 entry_guard 的 size 上限同源。
      金额 round $50 防 NAV 日波动 churn; 超 7 字符先降精度 (2.2k→2k) 再丢金额。

滞后(结构性, documented): exit_guards.json 由 brief 阶段刷新, 而 pipeline 顺序
reminders < brief 被测试钉死(brief 必须最后) → 画线用上一轮 guards (≤6h)。地板
只升不降 ⇒ 陈旧 = 偏低 = 报警偏晚, 权威触发仍是 exit_guard 每轮 heartbeat 真线。

所有权边界(关键): 判别 = 词表本身（备注空间给信息不给标记，
旧 "cc:" 前缀只认不写；上限 40 半宽单位，见 NOTE_WIDTH_MAX）
— note 恰为 stop/trim/gate/z1/g01 或 "stop:"/"z1:"/"抄底首档:"/"cc:"(legacy, 认旧不写旧) 开头
= 系统的, 可增删; 并入形态 "a/b" **每段**都须是系统词 (_owned_note);
其他备注(空/自定义文字) = 用户手动，永不触碰；
手动提醒与期望值完全相同(code+type+价位)时也不重复添加。裸 "stop" 是旧词，
认旧不写旧，一轮换血迁成 stop:type。
对账式: 期望集 vs 服务端现状 diff → ADD 缺的 / DEL 不再期望的系统条目;
价位同而 note 不同(授权变了/行使出组/legacy 迁移) → DEL+ADD 换血, note 也是对账对象。
get 失败 → **整轮放弃**(盲写会产生重复), 不做半份同步。

现价定向: 线在现价上方使用 PRICE_UP, 在现价下方使用 PRICE_DOWN。
价格穿越后提醒线翻向而不是消失, 继续表达回头穿越这一事件。`pin` 只控制
每票数量上限, 不再决定一条线是否持续存在。Snapshot 缺失时保留初始方向。

频率 ONCE_A_DAY(每级每天最多响一次), session 默认含盘前盘后(gap 也响)。
纪律: 提醒 = 情报/监控层通知, 不是授权 —— 响了之后做什么, 看卡片和手册。

CLI:
    python3 quant/sync/price_reminder_sync.py [--dry-run] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from datetime import date  # noqa: E402

from quant.core import book, earnings, env, future_gates, key_levels, setups, structure  # noqa: F401  (.env → os.environ; book = 组合占用真源; structure = regime 所有权真源; future_gates = 远端 gate 候选; key_levels = ⭐箱顶; setups = 止跌低)
from quant.core.holdings import live_tickers  # noqa: E402
from quant.core.io_json import read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import (# noqa: E402
    EXIT_GUARDS_PATH, OPTION_PLANS_PATH, POSITIONS_PATH, WATCHLIST_PATH,
    add_openapi_path)
from quant.core.timeutil import et_today  # noqa: E402
from quant.sync.today_group_sync import (# noqa: E402  (同层复用: Today 白名单推导)
    desired_members, grant_actual_cap)

LEGACY_PREFIX = "cc:"                       #  前的所有权前缀 — 认旧不写旧, 一轮换血迁移
# 抄底正股首档线备注前缀 — 写线与认领同读这一个常量。只写不认领会让首档每次重锚都
# ADD 新线、旧线被当手动永不 DEL (同票残留多条抄底线)。
BOTTOM_NOTE_PREFIX = "抄底首档:"
# ⭐ 箱顶线 / 止跌低线的备注词头 — 写线与认领同读这两个常量 (只写不认领会留下残线)。
KEY_NOTE_PREFIX = "⭐"
STOPLOW_NOTE_PREFIX = "止跌低"
KINDS = ("stop", "trim", "gate", "z1", "z2", "nochase", "g01", "oz1")   # oz1 = 期权选约首档限价; z2 = 二档下沿
# moomoo 服务端硬限 = 每票**每类型** 20 条, PRICE_UP / PRICE_DOWN 独立计数 —— 实测定界: PRICE_UP
# 第 21 条被拒, 服务端原文「The price alerts of type PRICE_UP for <T> have reached the limit of 20.
# You cannot add more within 10 minutes.」(撞限后该票该类型另有 10 分钟冷却); 同票 13 UP + 13 DOWN
# 共存被接受。⚠️ 旧「10 条/票」与 SDK docstring「每种类型最多 10 个」都是没探过上界的误记。
# 线随现价翻向 (orient_sides) ⇒ 最坏情形一票的线全挤在同一侧, 预算按单侧 20 算。
SERVER_MAX_PER_TYPE = 20
# moomoo 服务端写限速 = set_price_reminder **每 30 秒 60 次** —— 实测撞限, 服务端原文
# 「Set Price Alerts request failed due to high frequency. Maximum 60 times per 30 seconds.」
# 旧节流「每 10 次停 0.6s」只在差异 <60 条时碰巧够用 (平时每轮 5–10 条); 一次上百条的迁移会让
# 超出窗口的 ADD 全被拒, 而换血是先删后加 ⇒ 被删的旧线 (含 stop) 在服务端**消失**到下一轮。
SERVER_SET_MAX_PER_WINDOW = 60
SERVER_SET_WINDOW_S = 30.0


class _Throttle:
    """滑动窗口限速: 任意 `window` 秒内至多 `cap` 次写调用 (到顶就睡到最老那次滑出窗口)。
    clock / sleep 可注入 (测试用假时钟)。"""

    def __init__(self, cap: int, window: float, clock=time.monotonic, sleep=time.sleep):
        self.cap, self.window, self.clock, self.sleep = cap, window, clock, sleep
        self.stamps: List[float] = []

    def wait(self) -> None:
        now = self.clock()
        self.stamps = [s for s in self.stamps if now - s < self.window]
        if len(self.stamps) >= self.cap:
            self.sleep(self.window - (now - self.stamps[0]) + 0.5)
            now = self.clock()
            self.stamps = [s for s in self.stamps if now - s < self.window]
        self.stamps.append(now)
# 可裁线名额 12: 远端 gate 至多 1 (preview) + future_gates.FUTURE_GATES_MAX 条要名额 (深度住 core,
# 与 TG 阶梯表同读一个数); 画线补全后两桶都多了 z2 / 止跌低, watchlist 多了 stop / trim, 持仓多了
# z1 / reclaim 梯; pin 至多 4 条/票 (gate:cur/next + ⭐待破/⭐已破) ⇒ 最坏 12 + 4 = 16 条同侧 < 20,
# 仍给手动线留 4 条。
MAX_PER_TICKER = 12
# nochase 排在 z1 之后: z1 是带预算的可执行线, nochase 是同 regime 的参与上界。
# 同价同向撞线时标签并入不丢弃；服务端
# 同 (code,type,value) 只能一条, 丢线不可避免但标签都留); 并不进去 (同 kind 真重复 /
# 超宽) 才回退让位。优先级决定谁当 head (head 段 = 排序与所有权前缀的锚)。已知撞点:
# gate == no_chase (正常设计) / no_chase == trim 下沿 / no_chase == z1 上沿
# (单调律两端都允许相等) / stop == gate。
# fgate = 远端 gate (preview + 机械候选): 备注走 "gate:+N" 词头 (所有权靠 startswith("gate:")),
# 所以不进 KINDS (没有裸 "fgate" 备注); 优先级垫底 = 每票上限先裁它, 由远及近。
# key = ⭐ 线: 最高优先级 = 同价并入时它当 head (「极其关键」写在最前面); 它是 pin, 不占名额。
# stoplow = 止跌低: 紧跟 stop (都是「破了算错」的线); z2 排在 nochase 之后 (二档是 z1 的延伸)。
# 既有七类的相对次序不变 (stop < trim < gate < z1 < nochase < g01 < oz1 < fgate)。
PRIORITY = {"key": -1, "stop": 0, "stoplow": 1, "trim": 2, "gate": 3, "z1": 4, "nochase": 5,
            "z2": 6, "g01": 7, "oz1": 8, "fgate": 9}
STOP_SRC_PRI = {"card": 0, "gate": 1, "lock": 2, "trail": 3}   # 同价并列时的稳定序: 静态源优先 (gate = 期权 S5g)
G01_GAINS = (1.0, 2.0, 3.0)                 # 镜像 exit_guard PARAMS["g01_levels"] 的增益列
GUARDS_STALE_DAYS = 3                       # exit_guards as_of 超此天数 → 报出来 (仍用值: 只升不降 ⇒ 陈旧=偏低)

# note 宽度硬限 = 40 半宽单位（非 ASCII 计 2）：
# ascii ×40 OK / ×41 拒; "中"×20 OK / "中"×19+3A (41 单位) 拒; 服务端报错原文
# 「Enter no more than 20 Chinese characters」; round-trip 全部无截断。
# 旧「10 字符」限制并不准确；预算段仍另设显示宽度保险丝。
NOTE_WIDTH_MAX = 40


def _note_width(s: str) -> int:
    """moomoo 备注宽度：非 ASCII 计 2，服务端上限为 40 半宽单位。"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def _owned_note(note: str) -> bool:
    """所有权判别 = 词表本身: note 恰为 kind 或 "stop:"(binding 来源)/"z1:"(带预算)/
    "gate:"(cur|next 常驻线, +N 远端 gate)/"抄底首档:"(抄底正股首档)/"⭐"/"止跌低"/"cc:"(legacy)
    开头 = 系统的; 其他(空/自定义文字) = 用户手动, 永不触碰。
    并入形态 "a/b" 的每一段都须是系统词；"trim/nochase" 是，
    "trim/我的备注" 不是。边界只向「系统词的并入」扩, 手动线的误伤面不变。"""
    def seg_owned(s: str) -> bool:
        return (s in KINDS or s.startswith("stop:") or s.startswith("z1:")
                or s.startswith("gate:") or s.startswith("oz1:")
                or s.startswith(BOTTOM_NOTE_PREFIX)
                or s.startswith(KEY_NOTE_PREFIX) or s.startswith(STOPLOW_NOTE_PREFIX)
                or s.startswith(LEGACY_PREFIX))
    return bool(note) and all(seg_owned(s) for s in note.split("/"))


def _merged_note(win: Dict, lose: Dict) -> Optional[str]:
    """同价同向线的标签并入：
    服务端同价同向只能一条 ⇒ 丢线不可避免, 但标签可以都留 — "z1:11%:2k/nochase"。
    返回并入后 note; 并不进去 → None (回退让位): **同 kind 真重复** (reclaim 梯撞
    gate:cur 之类, 并入无新信息) 或 **宽度超 40**。"""
    win_kinds = {s.split(":", 1)[0] for s in str(win["note"]).split("/")}
    if lose["kind"] in win_kinds:
        return None
    cand = f"{win['note']}/{lose['note']}"
    return cand if _note_width(cand) <= NOTE_WIDTH_MAX else None


BUDGET_NOTE_MAX = 12       # 预算段显示宽度上限；见 NOTE_WIDTH_MAX 注，
                           # 12 覆盖全部现实格式 "40%:44.2k"=9, 一位小数 k 恒保留,
                           # 降级阶梯退化为纯保险丝)。"z1:"+12=15, 并入 "/nochase" 后
                           # 最宽 23 仍 < 40。


def _fmt_budget(frac: float, nav: float) -> str:
    """起步预算段 "2%:450" / "5%:1.1k" / "35%:6.8k" = 比例+数值。

    `frac` 是实际 cap 分数（已过冲击预算与 gross 余额），
    不是卡片上那个名义 2%/40% —— 见 `z1_budgets`。

    金额 round $50 防 NAV 日波动 churn。降级阶梯 (先降精度再丢金额) 保留为
    超 BUDGET_NOTE_MAX 的保险丝 — 现实格式全部 ≤9 字符, 正常永不触发。
    """
    pct = int(round(frac * 100))
    if nav * frac < 1:                    # 组合层余额用尽 = 真的 $0, 别用地板粉饰
        return f"{pct}%:0"
    # 财报反应日缩仓后 2%×0.24 = 0.47% → 四舍五入成 "0%", 和后面的金额自相矛盾
    # ("0%:100" 读起来像坏了)。<1% 用一个明确的记号, 不要装成 0。
    head = "<1%" if pct == 0 else f"{pct}%"
    v = max(50, int(round(frac * nav / 50.0)) * 50)

    def amt(digits: int) -> str:
        if v < 1000:
            return f"{v}"
        return f"{round(v / 1000, digits):g}k" if digits else f"{round(v / 1000)}k"

    for s in (f"{head}:{amt(1)}", f"{head}:{amt(0)}", head):
        if len(s) <= BUDGET_NOTE_MAX:
            return s
    return head[:BUDGET_NOTE_MAX]


_latest_nav = book.latest_nav      # NAV 读法全系统一份 (core.book), 不再各写各的


def z1_budgets(positions_doc: dict, watchlist_doc: dict, today: str,
               nav: Optional[float], usage: Optional[dict] = None,
               quote_of=None, calendar: Optional[dict] = None,
               veto_of=None) -> Dict[str, str]:
    """Today 白名单成员 → z1 note 预算段 {ticker: "2%:450"}。NAV 缺失 → 空
    (z1 回落成裸线, 由调用方报出来, 不静默)。

     (用户): 这个数是 **实际 cap**, 不是卡片上的名义 2%/40% ——
    先过冲击预算余额 (NAV 15% 的单日冲击上限) 与 gross 余额 (110% NAV), 三者
    取小。手机上响铃看到的金额, 必须和简报里 entry_guard 那条 size 上限同源,
    否则备注就是第二份真源。`usage` 不传 = 当空书算 (会高估), 调用方该传。
    """
    if not nav:
        return {}
    members, _ = desired_members(positions_doc, watchlist_doc, today)
    live = {t for t in live_tickers(positions_doc) if _is_stock(t)}
    out: Dict[str, str] = {}
    for t in members:
        # 卡源判别式与 _admit 同拍: live → positions 卡, 否则 → watchlist 卡。
        # 不能按 dict 有无 key 取 — positions 里的 closed 残卡 (qty 0 无 regime)
        # 会遮住 watchlist 真源 (AAOI 实证: 授权在组里, 备注却裸线)
        src = positions_doc if t in live else watchlist_doc
        ac = grant_actual_cap(t, (src.get("tickers") or {}).get(t) or {}, nav, usage,
                              quote_of, calendar, today, veto_of=veto_of,
                              exposure_of=lambda t: book.exposure_of(t, positions_doc))
        if ac:
            out[t] = _fmt_budget(ac["cap_frac"], nav)
    return out


def _is_stock(ticker: str) -> bool:
    """正股 ticker 全字母; OCC 期权 key(AAOI270617C140000)含数字 → 排除。"""
    return bool(ticker) and ticker.isalpha()


def _line(ticker: str, kind: str, rtype: str, value: Any) -> Dict:
    return {"code": f"US.{ticker}", "ticker": ticker, "kind": kind,
            "type": rtype, "value": round(float(value), 2), "note": kind}


def _current_gate(body: dict):
    """current gate = 把这张卡送进当前 regime 的那条轴 —— **只有转移机送进来的才算**。

    `last_regime.repair_gate.level` = `derive_observation_regime` 写的**被破掉的那条线**
    (label「收复 X = 回到上一 regime」)。它是**转移史**, 不是"我脚下的轴" ——
    canon 的判据真源 `structure.has_next_gate()` 也只认 `next_regime.entry_gate`。
    两者何时重合: 卡**经转移**回到线上时 (`source` ∈ auto/demoted, regime 就叫这个
    名字, stop 落在它下方 bfloor_buf −0.5N) —— NVDA 204 / MRVL 189 / INTC 98 皆是。

    🚨 `source == "card"` 时不成立 (用户  抓, NBIS 源案例): 那张卡是被
    **重画/促升**放在这个 regime 的("入场授权已行使"), 没有爬过任何 gate, 而它
    携带的 `last_regime` 是一个**从未经转移离开过的旧观察态**。NBIS 的
    observation_below_175.1 自己写着「cap 0 只作'价格下去大概到哪'的底」, 其
    repair_gate 175.1 = 破位线 = 8/06 裁决保持不动的 **stop 本身**—— 画出来
    是 stop 的复制品, 不是转移轴。**方向由现价定, 但"是不是一条轴"由来源定。**
    """
    a = body.get("agent") or {}
    if (a.get("current_regime") or {}).get("source") == structure.CARD_REGIME_SOURCE:
        return None
    lvl = coerce_float(((a.get("last_regime") or {}).get("repair_gate") or {}).get("level"))
    if lvl:
        return lvl
    hist = a.get("regime_history") or []
    return coerce_float(hist[-1].get("level")) if hist else None


def _next_gate(body: dict):
    ng = ((body.get("agent") or {}).get("next_regime") or {}).get("entry_gate") or {}
    return coerce_float(ng.get("level"))


def _pinned_gate_lines(ticker: str, body: dict) -> List[Dict]:
    """两条 gate 轴线(cur/next)。`pin` = 不被每票上限裁掉。

    初始 type 一律 PRICE_UP, 真方向由 `orient_sides` 按现价改写 —— 这里拿不到
    行情(纯函数, 价格在 sync() 里才拉), 也不该拿: 侧向是**一个**地方的事。
    """
    out: List[Dict] = []
    for level, tag in ((_current_gate(body), "cur"), (_next_gate(body), "next")):
        if not level:
            continue
        d = _line(ticker, "gate", "PRICE_UP", level)
        d["note"] = f"gate:{tag}"
        d["pin"] = True
        out.append(d)
    return out


def _future_gate_lines(ticker: str, body: dict, far: Optional[List[Dict]]) -> List[Dict]:
    """远端 gate 线: 卡上的 preview (gate+2) + 机械候选 (再往上)。非 pin。

    卡上未 stale 的 preview 恒为 +2; 机械候选的编号 `n` 由 `future_gates.for_card` 给
    (与 TG 阶梯表同一份, 不在这里再数一遍)。`far` = `future_gate_map` 的产出,
    None/空 = 本轮没有 K 线或没有地标, 只画卡上那条。"""
    out: List[Dict] = []
    lvl = future_gates.live_preview(body.get("agent"))
    if lvl:
        d = _line(ticker, "fgate", "PRICE_UP", lvl)
        d["note"] = "gate:+2"
        out.append(d)
    for c in far or []:
        d = _line(ticker, "fgate", "PRICE_UP", c["level"])
        d["note"] = f"gate:+{c['n']} {future_gates.evidence_tag(c)}"
        out.append(d)
    return out


def future_gate_map(positions_doc: dict, watchlist_doc: dict,
                    bars_of=book.bars) -> Dict[str, List[Dict]]:
    """每张正股卡 → 阶梯顶之上的机械远端候选 {ticker: [candidate]}。

    卡源判别式与 `z1_budgets` 同拍: live → positions 卡, 否则 → watchlist 卡
    (positions 里的 closed 残卡不许遮住 watchlist 真源)。没有 next gate 的卡没有轴可接 → 不出。"""
    live = {t for t in live_tickers(positions_doc) if _is_stock(t)}
    out: Dict[str, List[Dict]] = {}
    for src, names in ((positions_doc, live),
                       (watchlist_doc, set(watchlist_doc.get("tickers") or {}) - live)):
        for t in sorted(names):
            if not _is_stock(t):
                continue
            agent = ((src.get("tickers") or {}).get(t) or {}).get("agent") or {}
            if future_gates.ladder_top(agent) is None:
                continue                          # 没有轴可接 —— 连 K 线都不必读
            far = future_gates.for_card(agent, bars_of(t))
            if far:
                out[t] = far
    return out


def key_level_map(positions_doc: dict, watchlist_doc: dict,
                  bars_of=book.bars) -> Dict[str, Dict]:
    """每张正股卡 → {"top": 待破⭐箱顶|None, "brk": 近 60 根 ⭐突破|None, "stoplow": 止跌低|None}。

    卡源判别式与 `future_gate_map` 同拍 (live → positions, 否则 watchlist; moomoo_listed=False 不画)。
    **不看卡片 zones / 过期** —— 这三条线来自 K 线不来自卡, 卡过期照样画 (箱顶不因卡没重画而消失)。
    全空的票不出 key (下游 `.get` 即空)。"""
    live = {t for t in live_tickers(positions_doc) if _is_stock(t)}
    names = live | {t for t, b in (watchlist_doc.get("tickers") or {}).items()
                    if _is_stock(t) and (b or {}).get("moomoo_listed") is not False}
    out: Dict[str, Dict] = {}
    for t in sorted(names):
        bars = bars_of(t)
        if not bars:
            continue
        top = key_levels.box_top(bars)
        brk = key_levels.last_breakout(bars)
        bs = setups.bottom_setup(bars)
        low = coerce_float(bs.get("low")) if bs.get("stage") == "formed" else None
        stoplow = {"level": low, "signal_date": str(bs.get("signal_date") or "")} if low else None
        if top or brk or stoplow:
            out[t] = {"top": top, "brk": brk, "stoplow": stoplow}
    return out


def _key_note(kind: str, k: Dict) -> str:
    if kind == "top":
        return f"{KEY_NOTE_PREFIX}箱顶 {key_levels.evidence_tag(k)}"
    word = "已破" if k.get("intact") else "假突破"
    return f"{KEY_NOTE_PREFIX}{word} {str(k.get('date') or '')[5:]}"


def _mechanical_lines(ticker: str, info: Optional[Dict], drawn: List[Dict]) -> List[Dict]:
    """⭐ 两条 (pin) + 止跌低一条。`drawn` = 本票已生成的卡片线 —— ⭐ 的簇带 (下沿 − 桶宽, 上沿 + 桶宽)
    里已有卡片线 → **吸附到最近那条的价位** (之后同价并入, ⭐ 优先级最高当 head: "⭐已破 MM-DD/gate:cur");
    否则画在簇上沿。同一堵墙不画两遍 (与 future_gates 同一去重口径)。"""
    if not info:
        return []
    code = f"US.{ticker}"
    out: List[Dict] = []
    for kind in ("top", "brk"):
        k = info.get(kind)
        if not k:
            continue
        pad = float(k.get("bucket") or 0.0)
        near = [d for d in drawn if d["code"] == code
                and k["lo"] - pad <= d["value"] <= k["level"] + pad]
        value = (min(near, key=lambda d: abs(d["value"] - k["level"]))["value"]
                 if near else k["level"])
        d = _line(ticker, "key", "PRICE_UP", value)
        d["note"] = _key_note(kind, k)
        d["pin"] = True
        out.append(d)
    sl = info.get("stoplow")
    if sl and sl.get("level"):
        d = _line(ticker, "stoplow", "PRICE_DOWN", sl["level"])
        d["note"] = f"{STOPLOW_NOTE_PREFIX} ✓{sl['signal_date'][5:]}" if sl.get("signal_date") \
            else STOPLOW_NOTE_PREFIX
        out.append(d)
    return out


def _no_chase_line(ticker: str, zones: dict) -> List[Dict]:
    """当前 regime 的追价上限 → 一条 PRICE_UP (用户  补)。

    为什么它该有线: 卡上四条参与边界里, stop / z1 / trim 早就各有一条, 唯独
    `no_chase_above` 只活在简报与 gate 阶梯表里 —— 而它恰恰是**盘中**唯一会被
    快速穿过、且穿过后行为要立刻改变的那条 (上穿 = 不追市价, Rule #11)。
    heartbeat 6h 之间就是它最常被穿的窗口, 正是本模块存在的理由。

    **画的是 current regime 那条, 不是 next_regime 的**。这里天然只读
    `agent.zones`, 而 zones 恒等于当前 regime 的配置; 不得把尚未生效的 next
    no-chase 线混进来。

    **不 pin**: pin 只表示在每票数量上限里保留 gate。No-chase 与其他线一样
    随现价翻向, 不会在价格穿越后消失。
    """
    nc = coerce_float(zones.get("no_chase_above"))
    return [_line(ticker, "nochase", "PRICE_UP", nc)] if nc else []


def _z2_line(ticker: str, zones: dict) -> List[Dict]:
    """二档下沿 → 一条 PRICE_DOWN "z2" (补全, 两桶都画)。

    画下沿不画上沿: 上沿通常 = z1 下沿 (两带相接), 下沿才是新信息 —— 收破它 = zone Breakdown stale
    (当日收盘即作废) + 盘中 `z2_breach_buy_freeze` 当日 BUY 冻结, 是带内唯一会改变行为的那条边。"""
    z2 = zones.get("accumulation_zone_2") or []
    return [_line(ticker, "z2", "PRICE_DOWN", z2[0])] if len(z2) == 2 and z2[0] else []


def exit_floor_map(guards_doc: Optional[dict], positions_doc: dict) -> Dict[str, Dict]:
    """state/exit_guards.json → 每活跃持仓的下方地板候选。**只读已算好的值**
    (单一真源 = exit_guard; 不越价约束/armed 口径/HWM/parabolic 分支全在那边算完)。

    lock = ratchet.floor, basis = 自身 mark (正股=股价, 期权=期权价);
    trail = trail.trail, basis 恒 = underlying 收盘;
    gate = opt_gate_stop.level (期权 S5g, 用户: 授予 cap 的 gate 本身),
    basis = underlying 收盘, 只在期权 guard 上出现 (key 缺席 = 无此线)。
    guards 里可能有已平仓残留 (state 清理归 exit_guard), 本层按 live_tickers 过滤。"""
    out: Dict[str, Dict] = {}
    gpos = (guards_doc or {}).get("positions") or {}
    for t in live_tickers(positions_doc):
        g = gpos.get(t) or {}
        lock = coerce_float((g.get("ratchet") or {}).get("floor"))
        trail = coerce_float((g.get("trail") or {}).get("trail"))
        gate = coerce_float((g.get("opt_gate_stop") or {}).get("level"))
        if lock is not None or trail is not None or gate is not None:
            out[t] = {"lock": lock, "trail": trail}
            if gate is not None:
                out[t]["gate"] = gate
    return out


def _stop_pick(cands: List[Tuple[float, str]]) -> Tuple[float, str]:
    """下方线 binding 选择: 取 max; 同价并列 → 静态源优先 (card > lock > trail)。"""
    return max(cands, key=lambda c: (c[0], -STOP_SRC_PRI.get(c[1], 9)))


def g01_next_tier(lots: Optional[dict], mark: Optional[float]) -> Optional[float]:
    """期权 G-01 下一档 = 各 lot cost×(1+g), g∈{1,2,3}, 取高于现 mark 的最低档
    (全 lot 取 min = 下一个会响的档)。mark 缺失 → 最低档。已过 +300% → None。
    档位语义与 exit_guard.g01_crossings 同式 (lot cost 基); 本层用现 mark 判
    "已过", 不读 crossed 状态 — 提醒是预警, ONCE_A_DAY 自限噪。"""
    tiers: List[float] = []
    for tier in ("base", "position"):
        for lot in (lots or {}).get(tier) or []:
            cost = coerce_float(lot.get("avg_cost"))
            if not cost or cost <= 0 or (coerce_float(lot.get("qty")) or 0) <= 0:
                continue
            for g in G01_GAINS:
                thr = cost * (1 + g)
                if mark is None or mark < thr:
                    tiers.append(round(thr, 2))
                    break
    return min(tiers) if tiers else None


def desired_lines(positions_doc: dict, watchlist_doc: dict, today: str,
                  budgets: Optional[Dict[str, str]] = None,
                  floors: Optional[Dict[str, Dict]] = None,
                  option_plans: Optional[dict] = None,
                  far_gates: Optional[Dict[str, List[Dict]]] = None,
                  key_map: Optional[Dict[str, Dict]] = None) -> Tuple[List[Dict], List[str]]:
    """期望提醒集(纯函数, 可测)。返回 (lines, skip_notes)。
    key_map = key_level_map() 的产出 (⭐ 箱顶 / 止跌低); 缺省 = 不画机械线。
    budgets = z1_budgets() 的产出; 命中的 z1 note 带起步预算后缀。
    floors = exit_floor_map() 的产出; 下方线取三者 max, note 带 binding 来源。
    option_plans = entry_guard 落的选约 (用户): 授权 underlying (在
    budgets = Today 成员) 的合约 → **合约 code 上挂首档限价 PRICE_DOWN**, note
    "oz1:N张" — premium 跌到计划限价 = 参与点到了; 期权 basis 分开先例同 g01。
    far_gates = future_gate_map() 的产出; 缺省 = 只画卡上的 preview, 不画机械候选。"""
    lines: List[Dict] = []
    notes: List[str] = []
    floors = floors or {}
    live_all = sorted(live_tickers(positions_doc))
    live = {t for t in live_all if _is_stock(t)}

    # 下方线: 每 underlying 一条, 跨该 underlying 全部持仓取同基 max
    # (用户 裁决: max(卡 stop, 棘轮地板, trail), note = binding 来源)
    und_cands: Dict[str, List[Tuple[float, str]]] = {}
    for t in live_all:
        body = (positions_doc.get("tickers") or {}).get(t) or {}
        z = ((body.get("agent") or {}).get("zones")) or {}
        occ = parse_occ(t)
        und = occ["underlying"] if occ else t
        fl = floors.get(t) or {}
        contract_cands: List[Tuple[float, str]] = []   # 期权价基 (只挂合约 code)

        stop = coerce_float(z.get("stop_level"))
        if stop:
            if occ and (z.get("stop_level_basis") or "underlying") == "option_price":
                contract_cands.append((stop, "card"))
            else:
                und_cands.setdefault(und, []).append((stop, "card"))
        if fl.get("trail") is not None:                # trail 恒 underlying 基
            und_cands.setdefault(und, []).append((float(fl["trail"]), "trail"))
        if fl.get("gate") is not None:                 # 期权 S5g gate 线, underlying 基 ()
            und_cands.setdefault(und, []).append((float(fl["gate"]), "gate"))
        if fl.get("lock") is not None:                 # 棘轮地板 basis = 自身 mark
            (contract_cands if occ else und_cands.setdefault(und, [])) \
                .append((float(fl["lock"]), "lock"))

        if occ:
            if contract_cands:
                v, src = _stop_pick(contract_cands)
                d = _line(t, "stop", "PRICE_DOWN", v)
                d["note"] = f"stop:{src}"
                lines.append(d)
            nt = g01_next_tier(body.get("lots"),
                               coerce_float((body.get("broker") or {}).get("current_price")))
            if nt:
                lines.append(_line(t, "g01", "PRICE_UP", nt))
        else:
            trim = z.get("trim_zone") or []
            if len(trim) == 2 and trim[0]:
                lines.append(_line(t, "trim", "PRICE_UP", trim[0]))
            lines.extend(_no_chase_line(t, z))         # 当前 regime 的追价上限
            lines.extend(_pinned_gate_lines(t, body))  # 两条常驻 gate (cur/next)
            lines.extend(_future_gate_lines(t, body, (far_gates or {}).get(t)))   # 远端 gate (+2 …)
            for g in (z.get("reclaim_gates") or []):   # 本 regime 内的早信号梯 (持仓也画)
                if g.get("level"):
                    lines.append(_line(t, "gate", "PRICE_UP", g["level"]))
            # z1 回踩线。持仓与 watchlist 同义: 带预算 "z1:2%:450" = 有授权 (Today 成员);
            # **裸 "z1" = 无授权观察线, 不是动作触发点**。授权判据不动 (预算照旧只给 Today 成员),
            # 画线只管「看得见」—— 取代旧的「无授权持仓不画裸 z1」。
            z1 = z.get("accumulation_zone_1") or []
            if len(z1) == 2 and z1[1]:
                d = _line(t, "z1", "PRICE_DOWN", z1[1])
                if budgets and t in budgets:
                    d["note"] = f"z1:{budgets[t]}"
                lines.append(d)
            lines.extend(_z2_line(t, z))

    for und in sorted(und_cands):
        v, src = _stop_pick(und_cands[und])
        d = _line(und, "stop", "PRICE_DOWN", v)
        d["note"] = f"stop:{src}"
        lines.append(d)

    for t, body in sorted((watchlist_doc.get("tickers") or {}).items()):
        if not _is_stock(t) or t in live:
            continue
        if body.get("moomoo_listed") is False:
            continue
        z = ((body.get("agent") or {}).get("zones")) or {}
        if not z:
            continue
        vu = z.get("valid_until")
        if vu and str(vu) < today:
            notes.append(f"{t}: zones 过期 ({vu}) — 卡片线跳过, 重画后自动接上 (⭐/止跌低 照画)")
            continue
        for g in (z.get("reclaim_gates") or []):   # 本 regime 内的早信号梯 (不 pin)
            if g.get("level"):
                lines.append(_line(t, "gate", "PRICE_UP", g["level"]))
        lines.extend(_no_chase_line(t, z))         # 当前 regime 的追价上限
        lines.extend(_pinned_gate_lines(t, body))  # 两条常驻 gate (cur/next)
        lines.extend(_future_gate_lines(t, body, (far_gates or {}).get(t)))   # 远端 gate (+2 …)
        z1 = z.get("accumulation_zone_1") or []
        if len(z1) == 2 and z1[1]:
            d = _line(t, "z1", "PRICE_DOWN", z1[1])
            if budgets and t in budgets:      # Today 白名单成员: 响铃即见 比例+起步预算
                d["note"] = f"z1:{budgets[t]}"
            lines.append(d)
        # 补全 (无论是否有持仓): 候选卡的 stop / trim 此前只有持仓画。
        # stop = 卡片 regime 地板 (收破 = 本 regime 失效), 不是持仓止损; 备注沿用 binding 来源词 "stop:card"。
        stop = coerce_float(z.get("stop_level"))
        if stop:
            d = _line(t, "stop", "PRICE_DOWN", stop)
            d["note"] = "stop:card"
            lines.append(d)
        trim = z.get("trim_zone") or []
        if len(trim) == 2 and trim[0]:
            lines.append(_line(t, "trim", "PRICE_UP", trim[0]))
        lines.extend(_z2_line(t, z))

    # 期权选约首档限价 (授权 underlying 才画 — 与 z1 预算同一个白名单语义)
    for und, plan in sorted((option_plans or {}).items()):
        code = plan.get("code")
        t0 = (plan.get("tiers") or [{}])[0]
        if plan.get("entry_class") == "reversal_probe":
            # 抄底 (含正股腿): 授权住计划, 不看正股白名单; 信号过期不画。
            # 正股腿 = 首档限价线 (kind z1, 备注 抄底首档:股数), 合约腿 = oz1 线。
            vu = str(plan.get("valid_until") or "")
            if not vu or vu < today:
                notes.append(f"{und} 抄底 {code or '正股'} 信号已过期 (有效至 {vu or '?'}) — 线不挂")
                continue
            st = plan.get("stock") or {}
            if st.get("tiers") and st["tiers"][0].get("price"):
                d = _line(und, "z1", "PRICE_DOWN", st["tiers"][0]["price"])
                d["note"] = f"{BOTTOM_NOTE_PREFIX}{st['tiers'][0].get('shares') or '?'}股 止损{plan.get('support')}"
                lines.append(d)
            if not code or not t0.get("premium"):
                continue
            if str(plan.get("expiry") or "") <= today:
                notes.append(f"{und} 抄底 {code} 合约已到期 — oz1 线不挂")
                continue
        else:
            if not code or not t0.get("premium"):
                continue
            if str(plan.get("expiry") or "") <= today:
                notes.append(f"{und} 选约 {code} 已到期 — oz1 线不挂")
                continue
            if budgets is not None and und not in budgets:
                continue                          # 无授权不画 (裸 z1 补洞同拍)
        d = _line(code, "oz1", "PRICE_DOWN", t0["premium"])
        d["note"] = f"oz1:{t0.get('contracts') or '?'}"
        lines.append(d)

    # ⭐ 箱顶 / 止跌低: 机械线, 放在所有卡片线 (含持仓 stop 与抄底首档) 之后生成,
    # 才能吸附到同一堵墙上已有的那条卡片线。资格与 key_level_map 同拍: live 正股 / 自选内的候选。
    wl_ok = {t for t, b in (watchlist_doc.get("tickers") or {}).items()
             if _is_stock(t) and (b or {}).get("moomoo_listed") is not False}
    for t in sorted(key_map or {}):
        if t in live or t in wl_ok:
            lines.extend(_mechanical_lines(t, key_map[t], [d for d in lines if d["ticker"] == t]))

    # 去重 + 每票上限(按 kind 优先级, pin 先于同 kind 的普通线, 再按价位稳定排序)
    # pin 排前有两个作用: ①与 reclaim 梯同价时留下带 gate:cur/next 备注的那条
    # ②不占 MAX_PER_TICKER 名额 —— 上限只裁可裁的线。pin 至多 2 条/票 ⇒ 实际
    # 天花板 MAX_PER_TICKER+2 < SERVER_MAX_PER_TYPE (单侧 20), 仍给手动线留余量。
    seen: Dict[Tuple, Dict] = {}
    per: Dict[str, List[Dict]] = {}
    capped: Dict[str, int] = {}
    for d in sorted(lines, key=lambda d: (d["ticker"], PRIORITY.get(d["kind"], 9),
                                          0 if d.get("pin") else 1, d["value"])):
        key = (d["code"], d["type"], d["value"])
        win = seen.get(key)
        if win is not None:
            # 同价同向 → 排序保证 head 是高优先级那条; **标签并入不丢弃**
            # 能并入就保留两个标签，并不进去才回退
            # 让位并报出来 — 与 collapse_same_price 同一条纪律。
            if win.get("_capped"):
                notes.append(f"{d['ticker']}: {d['note']} {d['value']:g}"
                             f" 同价先行线已被每票上限裁掉 — 本条一并丢弃")
                continue
            merged = _merged_note(win, d)
            if merged:
                win["note"] = merged
                notes.append(f"{d['ticker']}: {d['note']} {d['value']:g} 与同价同向线并轨"
                             f" — 标签并入 → {merged}")
            else:
                notes.append(f"{d['ticker']}: {d['note']} {d['value']:g} 与已有同价同向线重复"
                             " — 让位给高优先级那条, 该价位仍有线 (并入放弃: 同 kind 或超宽)")
            continue
        seen[key] = d
        bucket = per.setdefault(d["ticker"], [])
        if not d.get("pin"):
            if capped.get(d["ticker"], 0) >= MAX_PER_TICKER:
                notes.append(f"{d['ticker']}: 超每票上限 {MAX_PER_TICKER}, 丢弃 {d['kind']} {d['value']}")
                d["_capped"] = True       # 后续同价线别再声称"该价位仍有线"
                continue
            capped[d["ticker"]] = capped.get(d["ticker"], 0) + 1
        bucket.append(d)
    out = [d for t in sorted(per) for d in per[t]]
    return out, notes


def orient_sides(desired: List[Dict], prices: Dict[str, float]) -> Tuple[List[Dict], List[str]]:
    """Orient every reminder around the latest price without dropping lines.

    A level above market is an upward-crossing reminder; a level at or below
    market is a downward-crossing reminder. Flips create new dictionaries and do
    not mutate the caller's desired-line objects. Missing snapshots pass through.
    """
    kept, notes = [], []
    for d in desired:
        lp = prices.get(d["code"])
        if lp:
            wanted = "PRICE_DOWN" if d["value"] <= lp else "PRICE_UP"
            if wanted != d["type"]:
                notes.append(f"{d['ticker']}: {d['note']} {d['value']:g} 现价 {lp:g}"
                             f" — 翻向 {wanted}(线常驻不丢)")
                d = {**d, "type": wanted}
        kept.append(d)
    return kept, notes


def collapse_same_price(desired: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """同 (code, type, 价位) 只能有一条服务端提醒 → 按 kind 优先级留一条,
    被让位方的标签并入留下那条（纯函数）。

    必须在 `orient_sides` **之后**跑: 定向前 gate:cur 是 PRICE_UP、卡 stop 是
    PRICE_DOWN(不同 key)，翻向后才可能撞上；z1 与 nochase 同理。

    此前撞了没人管: `plan_ops` 的 `want = {key: d for d in desired}` 是**后者覆盖**,
    而 desired 按优先级排序 ⇒ 低优先级的 gate 反而覆盖掉 stop, 且一声不响
    (又一例"字段在转换中被丢")。40 半宽单位内标签可合并为
    "stop:card/gate:cur"。并不进去
    (同 kind 真重复 / 超宽) 仍回退让位, 照旧播报。
    """
    best: Dict[Tuple[str, str, float], Dict] = {}
    notes: List[str] = []
    for d in desired:
        key = (d["code"], d["type"], round(float(d["value"]), 2))
        cur = best.get(key)
        if cur is None:
            best[key] = d
            continue
        win, lose = ((cur, d) if PRIORITY.get(cur["kind"], 9) <= PRIORITY.get(d["kind"], 9)
                     else (d, cur))
        head = win["note"]
        merged = _merged_note(win, lose)
        if merged:
            win = {**win, "note": merged}
            notes.append(f"{d['ticker']}: {lose['note']} {d['value']:g} 与 {head} 同价同向"
                         f" — 标签并入 → {merged}")
        else:
            notes.append(f"{d['ticker']}: {lose['note']} {d['value']:g} 与 {head} 同价同向"
                         f" — 留 {head}(优先级高), 该价位仍有线 (并入放弃: 同 kind 或超宽)")
        best[key] = win
    return list(best.values()), notes


def plan_ops(desired: List[Dict], current_rows: List[Dict]) -> Tuple[List[Dict], List[Dict], int, int]:
    """对账(纯函数): → (adds, dels, kept_ours, kept_manual_dup)。
    只删自己(_owned_note 词表判别); 手动条目与期望完全相同 → 不重复添加。
    系统条目价位同而 note 不同(z1 预算变了/授权行使/legacy cc: 迁移) → DEL+ADD
    换血 — note 承载授权展示后也是对账对象, 不然备注一旦落后就永远落后。"""
    def k(code: Any, rtype: Any, value: Any):
        return (str(code), str(rtype).upper(), round(float(value or 0), 2))

    owned, manual_keys = {}, set()
    for r in current_rows:
        key = k(r.get("code"), r.get("reminder_type"), r.get("value"))
        if _owned_note(str(r.get("note") or "")):
            owned[key] = r
        else:
            manual_keys.add(key)

    want = {k(d["code"], d["type"], d["value"]): d for d in desired}
    adds, stale_notes, kept_ours, kept_manual = [], [], 0, 0
    for key, d in want.items():
        if key in owned:
            if str(owned[key].get("note") or "") == d["note"]:
                kept_ours += 1
            else:
                stale_notes.append(owned[key])   # 换血: 先 DEL 旧 note 再 ADD 新
                adds.append(d)
            continue
        if key in manual_keys:
            kept_manual += 1          # 用户 手动已有同价位提醒, 不重复
            continue
        adds.append(d)
    dels = [r for key, r in owned.items() if key not in want] + stale_notes
    return adds, dels, kept_ours, kept_manual


def _sync(dry_run: bool) -> Dict:
    positions = read_json(POSITIONS_PATH)
    watchlist = read_json(WATCHLIST_PATH)
    nav = _latest_nav()
    # 组合占用整轮算一次 —— 逐票重算会让同一轮里前后两张卡看到不同的余额
    usage = book.load_usage(positions, nav)
    budgets = z1_budgets(positions, watchlist, et_today(), nav, usage,
                         calendar=earnings.load_calendar())
    guards_doc: Optional[dict] = None
    if EXIT_GUARDS_PATH.exists():
        try:
            guards_doc = read_json(EXIT_GUARDS_PATH)
        except Exception:                             # noqa: BLE001 — 读不了≠不同步, 降级说出口
            guards_doc = None
    floors = exit_floor_map(guards_doc, positions)
    opt_plans = ((read_json(OPTION_PLANS_PATH) or {}).get("plans")
                 if OPTION_PLANS_PATH.exists() else None)
    desired, skip_notes = desired_lines(positions, watchlist, et_today(), budgets,
                                        floors=floors, option_plans=opt_plans,
                                        far_gates=future_gate_map(positions, watchlist),
                                        key_map=key_level_map(positions, watchlist))
    if nav is None:
        skip_notes.append("NAV 缺失 (nav_history 空?) — z1 备注本轮不带预算, 裸线兜底")
    if guards_doc is None:
        skip_notes.append("exit_guards.json 缺失/不可读 — 下方线退化为卡片 stop"
                          " (exit_guard 布防了吗?)")
    else:
        as_of = str(guards_doc.get("as_of") or "")[:10]
        try:
            age = (date.fromisoformat(et_today()) - date.fromisoformat(as_of)).days
        except ValueError:
            age = None
        if age is None or age > GUARDS_STALE_DAYS:
            skip_notes.append(f"exit_guards as_of {as_of or '缺失'} 已 {age if age is not None else '?'} 天"
                              " — 地板/趋势线可能偏低 (exit_guard 在跑吗?), 本轮仍用旧值 (只升不降=偏保守低)")

    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK  # noqa: E402
    from moomoo import (Market, PriceReminderFreq, PriceReminderType,  # noqa: E402
                        SetPriceReminderOp)

    ctx = None
    try:
        ctx = create_quote_context()
        ret, df = ctx.get_price_reminder(market=Market.US)
        if ret != RET_OK:
            return {"ok": False, "error": f"get_price_reminder 失败: {df} — 整轮放弃(盲写会重复)",
                    "desired": len(desired)}
        rows = df.to_dict("records") if hasattr(df, "to_dict") else []

        codes = sorted({d["code"] for d in desired})
        prices: Dict[str, float] = {}
        if codes:
            rs, sdf = ctx.get_market_snapshot(codes)
            if rs == RET_OK and hasattr(sdf, "to_dict"):
                for row in sdf.to_dict("records"):
                    lp = row.get("last_price")
                    if lp:
                        prices[str(row.get("code"))] = float(lp)
            else:
                skip_notes.append(f"snapshot 失败 ({sdf}) — 保留初始方向, 由服务端校验兜底")
        desired, side_notes = orient_sides(desired, prices)
        skip_notes.extend(side_notes)
        desired, dup_notes = collapse_same_price(desired)   # 翻向后才可能撞价
        skip_notes.extend(dup_notes)

        adds, dels, kept, kept_manual = plan_ops(desired, rows)

        errors: List[str] = []
        done_add = done_del = 0
        if not dry_run:
            # 删与加共用一个窗口 (服务端按 set 调用计数, 不分 op); 留 5 次余量给同账户的手动操作
            throttle = _Throttle(SERVER_SET_MAX_PER_WINDOW - 5, SERVER_SET_WINDOW_S)
            for r in dels:                        # 先删, 释放每票配额
                rid = r.get("key")
                if rid is None:
                    errors.append(f"DEL 缺 key: {r.get('code')} {r.get('value')}")
                    continue
                throttle.wait()
                rt, data = ctx.set_price_reminder(code=r["code"], op=SetPriceReminderOp.DEL,
                                                  key=int(rid))
                if rt != RET_OK:
                    errors.append(f"DEL {r['code']} {r.get('value')}: {data}")
                else:
                    done_del += 1
            for d in adds:
                throttle.wait()
                rt, data = ctx.set_price_reminder(
                    code=d["code"], op=SetPriceReminderOp.ADD,
                    reminder_type=getattr(PriceReminderType, d["type"]),
                    reminder_freq=PriceReminderFreq.ONCE_A_DAY,
                    value=d["value"], note=d["note"])
                if rt != RET_OK:
                    errors.append(f"ADD {d['code']} {d['type']} {d['value']}: {data}")
                else:
                    done_add += 1
        return {"ok": not errors, "dry_run": dry_run,
                "desired": len(desired), "adds": len(adds), "dels": len(dels),
                "kept": kept, "kept_manual_dup": kept_manual,
                "done_add": done_add, "done_del": done_del,
                "add_lines": [f"+ {d['code']} {d['type']} {d['value']} ({d['note']})" for d in adds],
                "del_lines": [f"- {r.get('code')} {r.get('reminder_type')} {r.get('value')}"
                              f" ({r.get('note')})" for r in dels],
                "skipped": skip_notes, "errors": errors}
    finally:
        if ctx is not None:
            safe_close(ctx)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="卡片价位 → moomoo 价格提醒 对账同步")
    ap.add_argument("--dry-run", action="store_true", help="只算 diff 不写")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = _sync(a.dry_run)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        tag = "(dry-run) " if r.get("dry_run") else ""
        if not r.get("ok") and r.get("error"):
            print(f"⚠️ {r['error']}")
            return 1
        print(f"{tag}期望 {r['desired']} | +{r['adds']} / -{r['dels']} / 不变 {r['kept']}"
              + (f" / 手动同价 {r['kept_manual_dup']}" if r.get("kept_manual_dup") else ""))
        for ln in r.get("add_lines", []) + r.get("del_lines", []):
            print("  " + ln)
        for ln in r.get("skipped", []):
            print("  ~ " + ln)
        for ln in r.get("errors", []):
            print("  ✗ " + ln)
        # dry-run 看到 diff **不等于**差异被修掉了 ——  我在一个 session 里
        # 两次跑 dry-run、两次看到 `+ z1:35%:7k / - z1:5%:1k`, 把它读成"新代码算对了"
        # 的证据就走了。它同时也是"用户 手机上那个数是错的"的证据, 而我只读了前一半。
        # 用户 次日自己在 App 里发现 MSFT 还写着 5%。**诊断不是修复, 说出口。**
        if r.get("dry_run"):
            n = r.get("adds", 0) + r.get("dels", 0)
            print(f"⚠️ DRY-RUN — 服务端**未改动**。"
                  + (f"上面 {n} 条差异仍然存在, 手机上看到的还是旧值; "
                     "去掉 --dry-run 才会写 (或等下一轮 pipeline)。"
                     if n else "当前无差异。"))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
