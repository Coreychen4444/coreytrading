#!/usr/bin/env python3
"""自由度审计 —— **系统现在到底有多少个「可以调的数」？**

目的：审计 production 中已经存在的变量和自由度，避免凭感觉新增参数。
分类阈值可以由协议设定，但基线必须由脚本现场计算。

## 什么算一个自由度

**一个可以调、且调了会改变系统行为的数**= 1。判据三问：
  ① 它是**可调的**吗？（`0.40` 可以是 `0.35`；`len(rows)` 不是）
  ② 调了会**改变行为**吗？（播报宽度、日志格式不算）
  ③ 它是**定义**还是**选择**？定义不算 —— 价值区 70%（拍卖理论）、
     二项检验的 1.96、一年 252 个交易日，改它等于离开那套词汇表。

## 它统计什么

live 树（`core/ data/ sync/ decision/ options/ risk/ journal/ report/ ops/`）里：
  - 模块级 **ALL_CAPS 数值常量**（含数值元组/列表）
  - `PARAMS` / `THRESHOLDS` 类字典里的数值项
  - **函数签名的数值默认参数**（`mult: float = 3.0` 是货真价实的旋钮）

**不统计**：`research/`（旁路，不进 live）、`tests/`、判读层
（`chart_read`/`patterns` 由测试钉死改不了交易 —— 它们花注意力不花钱）。

⚠️ 扫描器只产出**候选**，`CLASSIFY` 里的分类是**人工的**（谁背书它、是不是定义）。
分类会过期 —— 新常量默认落进 `unclassified`，这就是它该被看见的地方。

Usage:
    python3 quant/research/dof_audit.py            # 汇总
    python3 quant/research/dof_audit.py --list     # 逐条
    python3 quant/research/dof_audit.py --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import ast  # noqa: E402
import json  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from quant.core.paths import ROOT  # noqa: E402

QUANT = ROOT / "quant"
LIVE_LAYERS = ("core", "data", "sync", "decision", "options", "risk",
               "journal", "report", "ops")
# 判读层：由 tests/report/test_chart_read.py 钉死「不写 state / 不 import 授权模块」
# ⇒ 它的旋钮改不了交易，不进本预算（它受注意力预算管，见 design §11.1）
JUDGMENT_ONLY = {"report/chart_read", "core/patterns"}

# **会动钱的那一层**—— 它的旋钮调错了会直接改变下单/仓位/止损。
# 其余（告警/播报/数据/管道）调错了改变的是注意力，不是钱。
# 这个划分决定预算该管谁：假发现的代价在这里，不在播报层。
MONEY_LAYERS = {
    "core/reversal",       # 抄底入场类判别式: 直接驱动正股/期权入场授权
    "core/setups",         # 抄底 ✓止跌 判别式 : reversal.detect 经它出信号 → 授权层
    "core/sizing", "core/structure", "core/episodes", "core/earnings", "core/book",
    "decision/entry_guard", "decision/exit_guard", "decision/regime_transition",
    "options/option_plan", "options/implied_move", "options/strike_compare",
    "risk/kelly_size", "risk/chain_layers",
    "sync/today_group_sync",          # 白名单 = 夜盘能不能下单
    "sync/price_reminder_sync",       # 画线 = 手机响铃 → 直接导致人下单
    "core/future_gates",              # 远端 gate 候选 → 同上那批线; 旋钮搬进 core 不等于免检
    "core/key_levels",                # ⭐ 箱顶线 → 同上那批线; 窗口全是既有常量别名, 零新旋钮
}

# 明显不是决策旋钮的名字（格式 / IO / 协议 / 索引）
SKIP_NAME_HINTS = ("TIMEOUT", "RETRY", "PORT", "WIDTH", "INDENT", "ENCODING",
                   "VERSION", "LIMIT_CHARS", "MAX_CHARS", "CHUNK", "SLEEP")

# ── 不是自由度（判据 ①②③ 至少一条不过） ──────────────────────
# 规则化排除，不逐条手列 —— 手列的清单会随代码漂，规则不会。
NOT_DOF_SUFFIX = ("_TOL", "TOL")                 # 浮点比较容差：不可"调"，只能对/错
NOT_DOF_CONTAINERS = ("PRIORITY", "STOP_SRC_PRI", "OPEN_SIDES", "CLOSE_SIDES",
                      "EMPTY_SUMMARY")           # 编码 / 排序，不是阈值
NOT_DOF_ARGNAMES = ("r", "timeout", "retries", "digits", "nd", "d", "delay",
                    "num", "limit", "max_results", "hours", "top_n",
                    "max_expiries")              # 管道：利率入参 / 网络 / 分页 / 格式
NOT_DOF_EXPLICIT = {                             # 外部约束，不是系统可调选择
    "sync/price_reminder_sync:NOTE_WIDTH_MAX",   # 券商 note 服务端硬限 40 半宽单位 (实测定界)
    "sync/price_reminder_sync:MAX_PER_TICKER",
    "sync/price_reminder_sync:SERVER_MAX_PER_TYPE",  # 券商服务端硬限 每票每类型 20 条 (实测定界)
    "sync/price_reminder_sync:SERVER_SET_MAX_PER_WINDOW",  # 券商服务端写限速 60 次 / 30 秒 (撞限实测)
    "sync/price_reminder_sync:SERVER_SET_WINDOW_S",
    "core/future_gates:_MIN_BARS",                   # 簇判别的最少历史 = 数据充分性守卫, 不是阈值
    "core/key_levels:_MIN_BARS",                     # 同上 (与 future_gates 同值同义)
    "decision/level_map:PROFILE_SESSIONS_PER_DAY",  # 一天几根 60m bar = 事实
    "report/portfolio_brief:TOP_MOVERS_COUNT",   # 显示条数
    "sync/trade_history_sync:fetch_history(days=)",
    "ops/pipeline:opend_alive(timeout=)", "ops/tg_notify:send_message(retries=)",
    "core/sizing:shock_budget_cap(used_budget=)",   # 缺省 0.0 = 可选入参, 不是旋钮
    "core/sizing:shock_budget_cap(used_gross=)",
    "decision/exit_guard:_klines_for(n=)",          # 载入几根 bar = 管道
    "options/option_plan:risk_free",                # 无风险利率 = 市场输入，不是系统可调选择
    "report/portfolio_brief:_cached_closes(n=)",
    "core/technical:_find_local_lows(window=)",     # 内部辅助, 由调用方口径决定
    "core/bottom_evidence:_EPS",                    # 级数截断精度 = 数值, 不是旋钮
}


# ── 人工分类（谁背书这个数） ────────────────────────────────────
#   ruled       有私人预注册回测或裁决依据
#   fiat        明确写入 production contract 的风险偏好
#   definition  定义常数，不是选择（改它 = 离开那套词汇表）
#   convention  本仓口径约定，跨模块一致性优先于最优性（如 ATR20 算钱/ATR14 读盘）
#   ops         运维/陈旧度阈值（决定"何时喊旧了"，不改交易方向）
#   unbacked    **没有出处的选择**← 这一类的大小才是本审计要回答的问题
#
# 🚨 **未在 CLASSIFY 里的一律计入 `unbacked`**—— 「没人记下它的出处」在操作上
# 就等于「没有出处」。想让某个数脱离这一类，去补 CLASSIFY 那一行（并附出处）。
CLASSIFY: dict[str, str] = {
    # ── 有裁决在案 ──
    "core/sizing:CAP_FRACS": "ruled",              # shock-budget protocol
    "core/sizing:NOTIONAL_CAP": "fiat",            # production contract
    "core/sizing:SHOCK_BUDGET": "fiat",            # production contract
    "core/sizing:SHOCK_X": "ruled",                # 1.88 池级常数, 回测量出
    "core/sizing:GROSS_CAP": "fiat",               # 110% (代价 −0.17pp 已量)
    "core/sizing:AT_RISK_CAP_FRAC": "fiat",        # locked portfolio contract
    "core/sizing:SHOCK_BREAKER_MULT": "fiat",      # locked portfolio contract
    "core/sizing:GATE_RISK_CAPS": "fiat",          # 累计 gate 风险预算
    "core/sizing:OPTION_ATRISK_LOSS_FRAC": "ruled",  # 0.25 锚 |S1|, pins 钉相等
    "decision/regime_transition:REL_FLOOR": "ruled",   # 0.4 relative_volume R
    "decision/regime_transition:RECLAIM_VOL_RATIO_MIN": "ruled",  # 1.1 — adaptive_volume_leg round-3 预注册回测; 此前的 1.2 是 convention, 本轮首次被测
    "decision/regime_transition:fastlane_k": "ruled",  # 0.5 ladder_entry 采纳
    # 量腿对齐日 = streak 内任一日 (volume_clock round-2 预注册回测;
    # +12 月复测为撤回条件)。
    # 缺省 1 = 旧口径 (快车道首日 / ×1 gate); 调用方按 streak_above 传真实 streak, 不是可调阈值。
    # 实现下沉到 volume_leg_detail (结构化转移收据), volume_leg 成为二元视图。
    # 同一个旋钮、同一份裁决 —— 登记两个符号是因为审计按符号扫, 不是因为多了一个自由度
    # (守卫 test_volume_leg_view_never_disagrees_with_its_structured_receipt 钉住两者不分叉)。
    "decision/regime_transition:volume_leg(streak=)": "ruled",
    "decision/regime_transition:volume_leg_detail(streak=)": "ruled",
    "decision/regime_transition:stop_days_default": "ruled",  # ×1「stop 破位收盘立即执行」+ round-2b bfloor ×1
    "decision/exit_guard:atr_multiple": "convention",  # 3×ATR trend-exit canonical
    "decision/exit_guard:parabolic_trail_pct": "convention",
    "decision/exit_guard:opt_s1_stop": "ruled",        # −25% options_exit_overlay
    "decision/exit_guard:opt_s2_days": "ruled",
    "decision/exit_guard:opt_s4_dte_floor": "ruled",
    "decision/exit_guard:opt_s3_iv_drop": "ruled",     # advisory
    "decision/entry_guard:repair_reclaim_offsets": "ruled",   # ladder_entry
    "decision/entry_guard:ladder_weights": "ruled",           # entry_ladder 50/30/20
    "core/episodes:LOOKBACK": "ruled",             # 60 gate_path 预注册
    "core/episodes:COOLDOWN": "ruled",             # 120
    "core/episodes:BROKE_WINDOW": "ruled",         # 决策点 F 锁定
    "core/episodes:DELTA_ATR": "ruled",            # 0.5 决策点 A 锁定
    "report/portfolio_brief:MOVE_ATR_MULT": "ruled",   # 1×ATR14 scale
    "report/portfolio_brief:MOVE_ATR_PERIOD": "convention",
    # 远端 gate 显示深度 —— 3 是显示约定: 只决定图上看得见多远, 零授权
    # (转移机只扫 entry_gate, 远端线不进任何 cap / sizing / 转移判据)
    "core/future_gates:FUTURE_GATES_MAX": "convention",
    "sync/price_reminder_sync:BUDGET_NOTE_MAX": "convention",  # 预算段的稳定显示上限
    # ── 定义 / 口径约定（不是选择） ──
    "core/technical:VALUE_AREA_FRAC": "definition",    # 70% 拍卖理论
    "core/technical:EDGE_WEIGHT": "unbacked",          # 0.05 三角权重下限, 无出处
    "core/earnings:EVENT_MULT": "ruled",               # 0.7 earnings_window 路 A
    "decision/level_map:TRAIL_ATR_MULT": "convention",     # = exit_guard, pin 钉
    "decision/level_map:TRAIL_PARABOLIC_PCT": "convention",
    # ── 教科书/行业标准参数（不是系统选择；修改后才成为自由度） ──
    "core/technical:rsi(period=)": "convention",           # 14 = Wilder 原始
    "core/technical:macd(fast=)": "convention",            # 12/26/9 = Appel 原始
    "core/technical:macd(slow=)": "convention",
    "core/technical:macd(signal_period=)": "convention",
    "core/technical:bollinger(period=)": "convention",     # 20/2σ = Bollinger 原始
    "core/technical:bollinger(std_mult=)": "convention",
    "core/technical:atr(period=)": "convention",           # ATR20 算钱 / 14 读盘 口径分工
    "core/technical:volume_ratio(period=)": "convention",
    "core/technical:ma_deviation(period=)": "convention",
    "core/book:ATR_PERIOD": "convention",
    "data/earnings_calendar:ATR_PERIOD": "convention",
    "report/gate_ladder_report:ATR_PERIOD": "convention",
    "decision/entry_guard:atr_period": "convention",
    "decision/exit_guard:atr_period": "convention",
    "decision/regime_transition:atr_period": "convention",
    "decision/level_map:turtle_n(period=)": "convention",
    "decision/regime_score:atr(period=)": "convention",
    "core/technical:analyze(rsi_period=)": "convention",
    # ── canonical 文档契约（不是回测，但有公开明文出处） ──
    "decision/exit_guard:ratchet_arm_gain": "fiat",        # +50% 保本 trend-exit §2
    "decision/exit_guard:ratchet_lock_gain": "fiat",       # +100% 回吐上限 1/3
    # IV 硬门槛 (配置裁决): entry_guard 旧两条播报带 (80/60) 退役,
    # 判别式下沉 core/sizing, 两个数 = 裁决 (percentile <40 / 绝对 ATM IV <50% 兜底)
    "core/sizing:LONG_CALL_IV_PCT_MAX": "fiat",
    "core/sizing:LONG_CALL_IV_ABS_MAX": "fiat",
    "decision/exit_guard:opt_gate_stop_days": "fiat",    # S5g ×1 日收盘确认
    "decision/entry_guard:parabolic_anchors": "ruled",     # entry_ladder 回测
    "decision/entry_guard:repair_reclaim_days": "ruled",   # 10 日 ladder_entry
    "decision/entry_guard:STALE_CONFIRM_DAYS": "convention",   # 2 日 zone-maintenance, pin
    "report/portfolio_brief:STALE_CONFIRM_DAYS": "convention",
    # Breakdown uses the first completed close; volatility uses a calibrated
    # per-instrument 2×ATR14 ruler. Pin tests keep both live consumers aligned.
    "decision/entry_guard:STALE_BREAKDOWN_DAYS": "ruled",
    "decision/entry_guard:STALE_VOL_ATR_MULT": "ruled",
    # 判别式下沉 core.structure.zone_mech_stale (MU 一张卡四个数案,
    # today_group/z1 画线接同判别式) —— 字面量搬家不是新旋钮, 出处同上四行:
    # 2/1/2.0 = 配置裁决 + 2,490 票日校准, 14 = zone-maintenance
    # 时间触发。eg/pb 侧现为 re-export, 三方恒等由 pin 测试钉。
    "core/structure:STALE_CONFIRM_DAYS": "convention",
    "core/structure:STALE_BREAKDOWN_DAYS": "ruled",
    "core/structure:STALE_VOL_ATR_MULT": "ruled",
    "core/structure:STALE_DAYS_REVIEWED": "convention",
    "decision/entry_guard:zone_stale_days_reviewed": "convention",  # 14 日 zone-maintenance
    "decision/entry_guard:FLOOR_GAP_ATR": "ruled",         # 0.25N round-2b
    "report/gate_audit:FLOOR_GAP_ATR": "ruled",
    "report/gate_audit:SWING_SKIP_ATR": "ruled",
    "report/gate_ladder_report:BFLOOR_BUF": "ruled",       # 0.5N round-2b 采纳
    "core/earnings:MIN_PRIOR": "ruled",                    # C 臂 earnings_window
    "core/earnings:POOL_X_EARN": "ruled",
    "core/earnings:SHRINK_N0": "ruled",
    "core/sizing:binding_stop(n_mult=)": "ruled",          # 2N 只兜底 risk_allocation
    # 分类轴退役 (, private preregistration): 旧三键
    # confirmed/probe/early_risky 随轮预算 dict→tuple 消失, 净 DoF −2
    # gate 语义：累计上限由 production contract 固定。
    # ROUND_RISK_FRACS(增量) → GATE_RISK_CAPS(累计) 是**同一个旋钮换表达**,
    # 不是新自由度 —— 三个数的出处仍是已锁定的 T × 50/30/20 (测试钉 provenance)。
    "core/sizing:GATE_RISK_CAPS": "ruled",                 # risk_allocation 三点锁定
    "core/sizing:OPTION_MIN_GATE": "fiat",                 # gate1 起
    # ── reversal_probe 入场类 (IVP 门 / 保费按 gate1 取、止损放在 gate 倒推 cap;
    # 判别式参数 = 探索定价锁定, 记录在私有研究记录; 改任一项须重跑 reversal_probe_backtest) ──
    # 现行信号 (试行, 试行期满按预先锁定判据复核):
    # 信号 = setups.bottom_setup ✓止跌 (次日未新低 / 长下影), 域 R0 (drawdown_max None), 止损 = 止跌低
    "core/reversal:newlow_lookback": "fiat",               # 20 日低 = 止跌低窗 (setups.LOW_LOOKBACK 同源)
    "core/reversal:high_lookback": "fiat",                 # 60 日高 (setups.HIGH_LOOKBACK 同源)
    "core/reversal:drawdown_min": "fiat",                  # ≥10% 才「跌到足够便宜」
    "core/reversal:valid_days": "ruled",                   # 10 (= setups.RECENT_SIGNAL_BARS): 位置受控后年龄无效
    "core/reversal:max_dist_atr": "ruled",                 # 2.0N 上限 (防离底太远、不上不下; 语义上限不是筛子)
    "core/reversal:LADDER_FRACS": "convention",            # 2/3 / 1/3 止跌低之上的二三档 (阶梯形状, 与 50/30/20 切法同源)
    "core/setups:RECENT_SIGNAL_BARS": "convention",        # ✓止跌 10 根内算刚形成 (判读列表窗; valid_days 与之对齐, 测试钉 ≤)
    "core/setups:DEEP_RETEST_FRAC": "definition",          # 2/3 = Rhea 次级折返带 (dow.RETRACE_BANDS 同源)
    "core/setups:NEAR_GATE_ATR": "convention",             # gate 之下 1N = 「即将形成」(判读)
    "core/setups:LH_LOOKBACK": "convention",               # 前高回看 = 60 日框架同窗
    "core/setups:SWING_WING": "convention",                # 5 根分形 (technical.swing_points 缺省)
    "core/setups:MIN_BARS": "convention",                  # 65 = 60 日框架 + 余量
    "core/setups:DECLINE_MIN_ATR": "ruled",                # 2N 一段下跌门 (防止当日创 20 日新高被标成顶后新低; 回放定价)
    "core/setups:ATR_TAIL_BARS": "convention",             # ATR20@止跌低 只算其前 120 根 (Wilder 种子影响 <1%; 回放不必每根全序列重算)
    "core/sizing:REVERSAL_PROBE_RISK_GATE": "fiat",        # 保费风险预算 = gate1 本档 2.5%
    # ── 底部确认概率 (判读件, 不在 MONEY_LAYERS: entry_guard 只印 [FYI] 行, verdict / cap 与它无关由测试钉) ──
    "core/bottom_evidence:BGK_BETA": "definition",         # −ζ(1/2)/√(2π) Broadie–Glasserman–Kou 离散监控修正
    "core/bottom_evidence:ANCHOR_TOL_ATR": "convention",   # 0.25N = interaction_census 同口径
    "core/bottom_evidence:CLUSTER_TOL_ATR14": "convention",  # 0.35×ATR14 = level_map.build_map 同值
    "core/bottom_evidence:PIVOT_LOOKBACK": "convention",   # 250 = interaction_census 同值
    "core/bottom_evidence:PROFILE_BARS": "convention",     # 60 = level_map profile_days 同值
    "core/bottom_evidence:HVN_TOP_N": "convention",        # 5 = level_map hvn_n 同值
    "core/bottom_evidence:MIN_TOUCHES": "convention",      # 簇 ≥2 触碰
    "core/technical:top_hvn(n=)": "convention",            # 自 decision/level_map 下沉, 同值 5
    "decision/entry_guard:RP_TARGET_DTE": "ruled",         # = LEAPS DTE 下限 270 (synthetic_longcall B1)
    # IV 分位代理 (HV20 的 252 日分位, 「要看 IVP」): 窗与可信线 = 口径约定
    # (iv_logger.percentile 同款 252; 120 = research 回测同口径), 阈值本身仍是 LONG_CALL_IV_PCT_MAX
    "core/technical:hv(period=)": "convention",
    "core/technical:hv_percentile(lookback=)": "convention",
    "core/technical:hv_percentile(min_samples=)": "convention",
    "core/book:HV_PCT_LOOKBACK": "convention",
    "core/book:HV_PCT_MIN_SAMPLES": "convention",
    # gate_no 由 `sizing.gate_count` 数出来 (判据 = 卡上给 cap 的 up 转移数),
    # 不是可调参数 —— 但它是入场侧的新入参, 记在册避免"看起来像凭空多出一个旋钮"。
    "decision/entry_guard:size_caps(gate_no=)": "convention",
    "options/option_plan:delta_band": "fiat",              # leaps-call-template §十三
    "options/option_plan:delta_band_high_iv": "fiat",
    "options/option_plan:delta_band_low_iv": "fiat",
    "options/option_plan:pick_expiry(min_dte=)": "ruled",  # 270 synthetic_longcall
    "options/option_plan:ladder_weights": "ruled",         # 50/30/20 同 entry_guard
    "sync/price_reminder_sync:G01_GAINS": "ruled",         # 镜像 exit_guard, pin 钉
    "decision/exit_guard:g01_levels": "fiat",              # G-01 档位 trend-exit canonical
    "decision/exit_guard:giveback_frac_low_dte": "fiat",   # 回吐上限 1/3 同族
    # ── 运维/陈旧度告警（不改交易，只决定"什么时候喊旧了"） ──
    "core/earnings:CALENDAR_STALE_DAYS": "ops",
    "decision/exit_guard:nav_stale_days": "ops",
    "sync/price_reminder_sync:GUARDS_STALE_DAYS": "ops",
    "report/portfolio_brief:UNTAGGED_FILL_WINDOW_DAYS": "ops",
    "report/portfolio_brief:calendar_stale_note(max_bdays=)": "ops",
    "journal/annotate_event:DECISION_WINDOW_DAYS": "ops",
    "journal/annotate_event:SIZE_TOL": "ops",
    "core/structure:ANCHOR_DRIFT_TOLERANCE_DAYS": "ops",
    "decision/exit_guard:process_reentry_watches(max_age_days=)": "ops",
    "data/iv_logger:percentile(window_days=)": "convention",
    "data/iv_logger:fetch_atm_iv(target_dte=)": "convention",
    "data/earnings_calendar:REACTION_LOOKBACK_DAYS": "convention",
    "data/earnings_calendar:TICKER_Q": "ruled",
}


def _num(node) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, (ast.Tuple, ast.List)):
        return bool(node.elts) and all(_num(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _num(node.operand)
    return False


def scan_module(path: Path, mod: str) -> list[dict]:
    """→ [{mod, name, kind, lineno, comment}]"""
    src = path.read_text()
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[dict] = []

    def comment_of(lineno: int) -> str:
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        return line.split("#", 1)[1].strip()[:70] if "#" in line else ""

    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        name = tgt.id
        if any(h in name for h in SKIP_NAME_HINTS):
            continue
        if _num(node.value) and name.isupper():
            out.append({"mod": mod, "name": name, "kind": "const",
                        "lineno": node.lineno, "comment": comment_of(node.lineno)})
        elif isinstance(node.value, ast.Dict) and name.isupper():
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and _num(v):
                    out.append({"mod": mod, "name": k.value, "kind": f"{name}[]",
                                "lineno": getattr(v, "lineno", node.lineno),
                                "comment": comment_of(getattr(v, "lineno", node.lineno))})
    # 函数签名的数值默认参数 —— 同样是旋钮
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        for arg, default in zip(args.args[len(args.args) - len(args.defaults):],
                                args.defaults):
            if _num(default) and not arg.arg.startswith("_"):
                out.append({"mod": mod, "name": f"{node.name}({arg.arg}=)",
                            "kind": "default", "lineno": node.lineno, "comment": ""})
    return out


def run() -> dict:
    items: list[dict] = []
    for layer in LIVE_LAYERS:
        d = QUANT / layer
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.py")):
            if p.name == "__init__.py":
                continue
            mod = f"{layer}/{p.stem}"
            if mod in JUDGMENT_ONLY:
                continue
            items.extend(scan_module(p, mod))
    kept: list[dict] = []
    dropped = 0
    for it in items:
        key = f"{it['mod']}:{it['name']}"
        name = it["name"]
        arg = name[name.find("(") + 1:-2] if it["kind"] == "default" else ""
        if (key in NOT_DOF_EXPLICIT
                or name.endswith(NOT_DOF_SUFFIX)
                or any(c in it["kind"] for c in NOT_DOF_CONTAINERS)
                or (arg and arg in NOT_DOF_ARGNAMES)):
            dropped += 1
            continue
        it["class"] = CLASSIFY.get(key, "unbacked")
        it["money"] = it["mod"] in MONEY_LAYERS
        kept.append(it)
    items = kept
    by_class = Counter(it["class"] for it in items)
    money = [it for it in items if it["money"]]
    money_class = Counter(it["class"] for it in money)
    by_mod: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        by_mod[it["mod"]][it["class"]] += 1
    return {
        "total": len(items), "dropped_not_dof": dropped,
        "by_class": dict(by_class),
        "by_kind": dict(Counter(it["kind"] for it in items)),
        "money_total": len(money), "money_by_class": dict(money_class),
        "money_unbacked": sorted(f"{it['mod']}:{it['name']}"
                                 for it in money if it["class"] == "unbacked"),
        "by_module": {m: dict(c) for m, c in sorted(by_mod.items(),
                                                    key=lambda kv: -sum(kv[1].values()))},
        "items": items,
    }


def render(r: dict, listing: bool = False) -> str:
    L = ["自由度审计 —— live 树里「可以调的数」有多少",
         f"（不含 research/ · tests/ · 判读层 {sorted(JUDGMENT_ONLY)}）", ""]
    L.append(f"扫描候选 {r['total'] + r['dropped_not_dof']} 项，"
             f"按判据①②③排除**非自由度**{r['dropped_not_dof']} 项"
             f"（容差/排序/编码/API/格式/利率入参）")
    L.append(f"→ **真实自由度候选 = {r['total']}**")
    L.append("按来源分类（分类是人工的，见 CLASSIFY）：")
    order = ["ruled", "fiat", "convention", "definition", "ops", "unbacked"]
    cn = {"ruled": "有裁决/回测在案", "fiat": "production contract 风险偏好",
          "convention": "本仓口径约定/教科书标准", "definition": "定义常数（不算自由度）",
          "ops": "运维/陈旧度阈值", "unbacked": "**无出处的选择**"}
    for k in order:
        if k in r["by_class"]:
            L.append(f"   {cn[k]:26} {r['by_class'][k]:>4}")
    L.append("")
    L.append(f"🚨 **会动钱那一层**（授权/仓位/退出/画线）：{r['money_total']} 个自由度")
    for k in order:
        if k in r["money_by_class"]:
            L.append(f"   {cn[k]:26} {r['money_by_class'][k]:>4}")
    L.append("")
    L.append("按模块（前 12）：")
    for m, c in list(r["by_module"].items())[:12]:
        L.append(f"   {m:34} {sum(c.values()):>4}   " +
                 " ".join(f"{k}={v}" for k, v in sorted(c.items())))
    if listing:
        L.append("")
        L.append("逐条：")
        for it in sorted(r["items"], key=lambda x: (x["mod"], x["name"])):
            L.append(f"   [{it['class']:12}] {it['mod']:30} {it['name']:34} "
                     f"{it['kind']:10} {it['comment']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="自由度审计（live 树可调常量普查）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run()
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render(r, a.list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
