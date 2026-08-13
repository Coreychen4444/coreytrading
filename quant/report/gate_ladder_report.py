#!/usr/bin/env python3
"""收盘 gate 阶梯日报 — 持仓 + watchlist 全员 vs 各自 gate 阶梯的机械快照 (用户  立项;
 加持仓 section — 持仓卡同样带 reclaim/next_regime 阶梯, 只报 watchlist 是覆盖缺口)。

定位: 每个美股交易日收盘后 (05:10 SGT 定时任务) 推送 Telegram 的**代码渲染表**。
规矩同 fired_cards: 原文推送, 不许叙述层改写/压缩 —— prose 会自己补字段, 补出来的都是错的。

每票渲染: 收盘价/涨跌/量比 (technical.volume_ratio 口径, 含完成日; ⚡ = 个股自身放量
≥1.2, 仅 tape 读数 — **gate 量能闸自  起为相对口径**volume_leg, 见各 gate 行)
+ 阶梯逐级状态 (✅ 站稳 n/n 成立 | ⏳ 计数中 m/n | ▫️ 未触及, streak 口径 =
entry_guard._gate_streak, 只数已完成收盘; 卡片 gate 写了 volume_ratio_min 且最后
完成 bar 量腿未达 → ✅ 行追加 ⚠️量能闸未过, 同 regime_transition.volume_leg 口径)
+ zone1 位置 + 约束/双闸提示 (🔒 走 sizing.constraint_lines canonical, from/until
窗口三态 [窗口未开]/[已过期] 原样播报;  修 — 此前手搓渲染丢 from,
把未开窗的 NBIS 财报 clamp 播成了 binding)
+ **gate 距离双单位 %(N) + ⚡ 快车道读数**(用户 要求, 见 §gate 距离)。

线名 = gate 的**结构身份**四分类 (换掉旧的 trend/lsh 两档启发式 —— 旧规则
让 9 张卡里 8 张标成修复/收复线, 用户:「永远只有修复路径一个语义」)。判别式真源
`core.episodes.gate_path`, 与 `gate_path_taxonomy_backtest` 同一份。
⚠️ **只是名字, 不是资格**: 四分类  裁决全部不采纳, ⚡ 判据一个字没改。

gate 成立 ≠ 已授权入场: 转移由 `regime_transition.run()` 机械执行 (**两个桶都扫**
——  裁决 A 之后 watchlist 卡与 positions 卡同样自动切; 本文件此前写着
"watchlist 不在处理范围" 是 8/05 前的旧话,  改); provisional 带补画锁,
补画前 entry_guard 不授权。本报告只报事实不发授权。

只读: 不碰告警冷却 (heartbeat-state), 不落 decisions, 不写任何 state。
数据 = kline 缓存 (完成 bar; 任务先跑 kline_cache 刷新) + watchlist.json + premium 落库。

Usage: python3 quant/report/gate_ladder_report.py [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
from typing import List, Optional, Tuple

from quant.core import technical  # noqa: E402
from quant.core.episodes import (# noqa: E402  gate 结构身份: 与回测同一份判别式
    DELTA_ATR, MIN_BARS, breakdown_in_window, episodes_of, gate_path, merge_bars_by_date)
from quant.core.holdings import is_managed, live_tickers  # noqa: E402  「谁算持仓」单一真源
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import (# noqa: E402
    KLINE_CACHE_DIR, KLINE_DEEP_DIR, MARKET_DIR, POSITIONS_PATH, WATCHLIST_PATH)
from quant.core.sizing import constraint_lines, read_constraints  # noqa: E402  🔒 canonical, 不另造
from quant.decision.entry_guard import _gate_streak  # noqa: E402  同一 streak 口径, 不另造
from quant.decision.regime_transition import PARAMS as RT_PARAMS  # noqa: E402
from quant.decision.regime_transition import volume_leg  # noqa: E402  量腿 canonical (rel 口径), 不另造

VOL_GATE = 1.2
# ⚡ 幅度快车道 (ladder_entry  裁决 k=0.5 采纳, 用户 批「都做」):
# 首日收盘 > gate + 0.5×ATR20 且量腿过 → **当日转移, 免 ×2**, 随后 entry_guard
# 给 repair_reclaim 三档。**数值不在本文件存第二份**—— 读 regime_transition。
#
# ⚠️ 命名坑 (踩到): 「快车道」在 `fastlane_repair_gate_design.md` 是
# **不采纳**(幅度→D1 全额单点入场), 在 `ladder_entry_design.md` 是 **采纳**
# (同场景→D1 分档 ladder)。否掉的是"全额提前", 采纳的是"分档提前", 不是一回事。
#
# ⚠️ 只有 `next_regime.entry_gate` 会被 detect_transition 扫 —— 止跌线
# (reclaim_gates) 与修复线 (next_gate_preview) **转移机根本不看**, 给它们标 ⚡
# 就是编一个不存在的路径。判据 = ladder_of 的 `kind == "entry"`。
FASTLANE_K = RT_PARAMS["fastlane_k"]
ATR_PERIOD = 20              # 算钱尺 (technical.atr 口径分工: ATR20 算钱 / ATR14 读盘)
BFLOOR_BUF = 0.5             # 转移后地板 = gate − 0.5×ATR20 (round-2b, 用户  批)
# 板块量能 gauge (用户  加): SMH = 半导体板块唯一量能标的裁决
# (SOX 是指数无真实量, SOXX 流动性劣于 SMH; macro-context-check 房规首选 SMH)。
# 定性标签为机械四象限, 展示用 advisory, 不进任何判定。
# (指数, ETF 量能代理, 标签)。指数管涨跌 (真指数, Yahoo 源, 用户  加
# .NDX/.SPX/.SOX/.VIX 进分组求每日大盘读数); ETF 管量比 (指数无成交量语义)。
# 指数缓存缺失时退回 ETF 代理报涨跌 — 表头永不因单一数据源断供而消失。
SECTOR_GAUGES = (("NDX", "QQQ", "纳指"), ("SPX", "SPY", "标普"), ("SOX", "SMH", "费半"))
TAG_CHG_MIN = 1.5      # |涨跌%| ≥ 此值才定性
TAG_VOL_LOW = 0.8      # 量比 < 0.8 = 缩量


def sector_tag(chg: float, vr: Optional[float]) -> str:
    """机械四象限定性: 涨跌方向 × 量能档位。阈值未达 → 空串。"""
    if vr is None or abs(chg) < TAG_CHG_MIN:
        return ""
    if chg > 0:
        return "放量上涨" if vr >= VOL_GATE else ("缩量反弹⚠️" if vr < TAG_VOL_LOW else "常量上涨")
    return "放量下跌⚠️" if vr >= VOL_GATE else ("缩量回调" if vr < TAG_VOL_LOW else "常量下跌")


def sector_lines(kdir: Path = None) -> List[str]:
    kdir = kdir or KLINE_CACHE_DIR
    out = []
    for idx, etf, label in SECTOR_GAUGES:
        ibars, ebars = load_bars(idx, kdir), load_bars(etf, kdir)
        src, name = (ibars, idx) if len(ibars) >= 2 else (ebars, etf)
        if len(src) < 2:
            continue
        close = coerce_float(src[-1].get("close"))
        prev = coerce_float(src[-2].get("close"))
        if not close or not prev:
            continue
        chg = (close / prev - 1) * 100
        vr = vol_ratio_last(ebars) if len(ebars) >= 21 else None
        vr_s = f"{vr:.2f}×{'⚡' if vr >= VOL_GATE else ''}" if vr is not None else "?"
        tag = sector_tag(chg, vr)
        out.append(f"🏭 {label} {name} {close:,.0f} ({chg:+.1f}%) | {etf} 量比 {vr_s}"
                   + (f" — {tag}" if tag else ""))
    vb = load_bars("VIX", kdir)
    if len(vb) >= 2:
        vc, vp = coerce_float(vb[-1].get("close")), coerce_float(vb[-2].get("close"))
        if vc and vp:
            out.append(f"🌡 VIX {vc:.1f} ({vc - vp:+.1f})")
    return out


def load_bars(tk: str, kdir: Path = KLINE_CACHE_DIR) -> List[dict]:
    p = kdir / f"{tk}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def deep_bars(tk: str, kdir: Path = KLINE_CACHE_DIR,
              ddir: Path = KLINE_DEEP_DIR) -> Tuple[List[dict], Optional[str]]:
    """给 gate 结构身份判别式用的**深序列**= 深度缓存 + 常规缓存, 按日期去重合并。

    为什么不能只用常规缓存: 它只有 ~1 年 (221–291 根)，而 `gate_path` 的
    `ath` 是「历史最高收盘」、`breakdown_in_window` 要 180 根才算得满。
    只拿一年的高点当 ATH，会把一堆普通 gate 判成「新高线」。

    🚨 **必须去重, 不能直接拼**: 两段重叠 200+ 日, 重复 bar 会污染 MA60
    (实测踩过)。合并交给 `core.episodes.merge_bars_by_date` ——
    与回测同一份实现。深度缓存**不入 git 且会过期**, 所以断层要报出来不能静默。
    """
    d = load_bars(tk, ddir)
    reg = load_bars(tk, kdir)
    if not d:
        return reg, "无深度缓存"
    bars, note = merge_bars_by_date(d, reg)     # 深度在前、常规在后 ⟹ 重叠段以常规为准
    return bars, note


# ── gate 线名: 四分类换名 (用户 要求) ───────────────────
#
# 旧规则只有两档 —— `trend=="up" → 突破线` / `level >= 动态 lsh → 修复线` ——
# 于是 9 张 live 卡里 8 张被标成修复/收复线。用户 :
# 「我看 gate ladder report 永远只有修复路径一个语义」。
#
# 新规则 = `core.episodes.gate_path` 的四分类判别式 (与 gate_path_taxonomy 回测
# **同一份实现**)。差别在于旧规则用 trend/lsh 猜"是不是修复路径", 新规则**直接问
# 破位史**: 最近 120 交易日内有没有破到这条 gate 之下。实测这一条就纠正了
# MRVL 223 / MU 945 —— 卡片名叫 `B_repair_probe_*`, 但窗内 0 天深度破位, 它们是箱体顶。
#
# ⚠️⚠️ **四分类不携带授权含义。**裁决 = 四类**全部不采纳**
# (②③④ 上 `_fastlane` 维持 ×2)。这里换的只是**名字**, 不是资格:
# 看到「箱体顶」不表示系统对它做了任何授权判断, ⚡ 的条件一个字没改。
PATH_TAGS = {"uptrend": "突破线", "range_top": "箱体顶", "ath": "新高线"}
RETAG_KINDS = ("entry", "preview")   # 止跌线 (reclaim) 按构造就在修复路径上, 不改名


def gate_path_of(level: float, bars: List[dict], trend: Optional[str],
                 atr20: Optional[float]) -> Optional[str]:
    """这条 gate 的结构身份。None = 序列不够深 ⇒ 不给身份 (调用方回落旧规则)。"""
    if atr20 is None or len(bars) < MIN_BARS:
        return None
    closes = [coerce_float(b.get("close")) for b in bars]
    if any(c is None for c in closes):
        return None
    ma60 = technical.sma(closes, 60)
    eps = episodes_of(closes, ma60)
    at = len(closes)                       # live: "此刻"就是最后一根之后
    broke, _, _ = breakdown_in_window(eps, closes, at, level)
    return gate_path(broke, trend, level, max(closes), DELTA_ATR * atr20)


def retag(g: dict, cls: Optional[str], lsh: Optional[float],
          uptrend: bool = False) -> dict:
    """按结构身份给 gate 行换名。

    `cls is None` = 序列不够深, 算不出身份 → **回落旧的两档规则整条**
    (`trend=="up"→突破线` + `level ≥ lsh→修复线`)。
    ⚠️ 回落必须是**完整**的旧规则: 第一版只留了 lsh 那半, 短序列的 up 趋势卡当场
    掉回"收复线" —— 拿不到新能力时应当退回原样, 不是退回半个原样。
    """
    if g["kind"] not in RETAG_KINDS:
        return g
    if cls in PATH_TAGS:
        return dict(g, tag=PATH_TAGS[cls])
    if cls is None and uptrend and g["tag"] in ("收复线", "修复线"):
        return dict(g, tag="突破线")
    # ①修复 (或回落): 保留修复路径三词, 沿用动态 lsh 的收复→修复细分
    if lsh and g["tag"] == "收复线" and g["level"] >= lsh:
        return dict(g, tag="修复线")       # ≥ 动态 lsh 的 gate 完成结构修复 (语义分层)
    return g


def vol_ratio_last(bars: List[dict]) -> Optional[float]:
    """完成日量比 (technical.volume_ratio 同口径: SMA20 含完成日)。"""
    vols = [int(b.get("volume") or 0) for b in bars]
    ratios = technical.volume_ratio(vols, 20)
    return ratios[-1] if ratios and ratios[-1] is not None else None


def ladder_of(agent: dict) -> List[dict]:
    """卡片阶梯统一形状: [{level, days, tag, label}] 按价位升序, entry/preview 去重合并。"""
    # 语义分层 (用户 定): gate 行只用结构层词汇 (止跌/收复/修复),
    # probe/confirmed 是仓位层词汇, 档位由结构判别式定 (v3  纯结构单腿), 不渲染在 gate 行上。
    # 数据里的 unlocks 字段自此不再渲染 (display-only 遗留, 随各卡重画自然淘汰)。
    # `kind` = **数据层身份**, 与 `tag` (显示名, 会随趋势换词) 分开: 只有
    # kind=="entry" 的那道会被 detect_transition 扫, 因此只有它能走 ⚡ 快车道。
    rows = {}
    for g in ((agent.get("zones") or {}).get("reclaim_gates") or []):
        lvl = coerce_float(g.get("level"))
        if lvl is not None:
            rows[lvl] = {"level": lvl, "days": int(g.get("days") or 2),
                         "tag": "止跌线", "kind": "reclaim",
                         "label": str(g.get("label") or "")[:24],
                         "vol_min": coerce_float(g.get("volume_ratio_min"))}
    nr = agent.get("next_regime") or {}
    eg = nr.get("entry_gate") or {}
    lvl = coerce_float(eg.get("level"))
    if lvl is not None:
        rows[lvl] = {"level": lvl, "days": int(eg.get("days") or 2),
                     "tag": "收复线", "kind": "entry",
                     "label": str(nr.get("name") or "")[:24],
                     "vol_min": coerce_float(eg.get("volume_ratio_min")),
                     "next_stop": coerce_float((nr.get("zones") or {}).get("stop_level"))}
    pv = nr.get("next_gate_preview") or {}
    lvl = coerce_float(pv.get("level"))
    if lvl is not None:
        rows[lvl] = {"level": lvl, "days": int(pv.get("days") or 2),
                     "tag": "修复线", "kind": "preview", "label": "",
                     "vol_min": coerce_float(pv.get("volume_ratio_min"))}
    return [rows[k] for k in sorted(rows)]


# ── gate 距离 + ⚡ 快车道播报 (用户 要求「达标线可加多一些信息」) ──
#
# 为什么距离必须**双单位**: 今日实测 MSFT 535 距 +6.5% = 2.33N, MU 945 距 +7.1%
# = 0.82N —— 百分比几乎一样, 真实距离差 2.8 倍 (ATR/价 3.0% vs 9.4%)。只看 %
# 会把最远的那条读成"快到了"。同  异动阈值那一课: 全池一把尺 = 没有尺。

def vol_short(note: Optional[str], ok: bool) -> str:
    """量腿的**紧凑**播报: `量腿 4.27✓`。

    `volume_leg` 的完整 note 带基准出处 (`量比(rel SMH 0.52) 4.27`), 但那个基准
    每行都一样、且已经印在 🏭 板块表头里 —— 逐行重复它只是把手机上的行撑爆。
    **例外: `abs兜底` 必须原样保留**—— 那是降级读数, 降级不静默 (relative_volume
    裁决 R 的原话)。
    """
    if not note:
        return ""
    mark = "✓" if ok else "✗"
    if "abs兜底" in note:
        return f"{note}{mark}"
    num = note.rsplit(" ", 1)[-1]
    return f"量腿 {num}{mark}"


def gate_distance(close: float, atr: Optional[float], level: float) -> str:
    """price 相对 gate 的距离段。gate 在上 → "距 +x%"; 在下 → "上方 +x%"。

    ⚠️ 两个方向的**分母不同, 这是有意的**—— 各自都是"从你现在站的地方算":
      gate 在上: `level/close − 1` = 从**现价**还要涨多少才够到 (可执行)
      gate 在下: `close/level − 1` = 已经站在线**上方**多少 (与 📍 trim 行同口径)
    首版按 level 统一归一, 把旧的 "距 +3.9%" 改成了 +3.7% —— 被既有测试当场抓到。
    """
    if close is None or level is None or not close:
        return ""
    n = f" ({abs(close - level) / atr:.2f}N)" if atr else ""
    if close > level:
        return f"上方 +{(close / level - 1) * 100:.1f}%{n}"
    return f"距 +{(level / close - 1) * 100:.1f}%{n}"


def fastlane_state(bars: List[dict], close: float, atr: Optional[float],
                   g: dict, streak_n: int, ticker: str,
                   cls: Optional[str] = None) -> Optional[dict]:
    """⚡ 四条件逐条读, **与 regime_transition._fastlane 同拍**。None = 不适用。

    `cls` = 本 gate 的结构身份 (gate_path_of, render_ticker 已为线名算过一次)。
    ⓪域 (接线): 非①修复 → 只返回 {"domain": cls} —— live `_repair_domain`
    会拒发快车票, 报表若继续渲染「⚡ ≥X 免 ×2」就是在预告一条不存在的路
    (R4 那类「两个渲染层不同步」)。cls=None (判不了) 镜像 live 的保守拒绝。
    返回 {domain, trigger, over_n, first_day, mag_ok, vol_ok, vol_note}。
    首日判据与 `_fastlane` ①(昨在下今在上) 等价 = `streak_n == 1`。
    """
    if g.get("kind") != "entry" or not atr or close is None:
        return None
    if cls != "repair":                                    # ⓪ 域外/不可判 → 无快车道
        return {"domain": cls}
    lvl = g["level"]
    vol_ok, vol_note = volume_leg(bars, g.get("vol_min"), ticker)
    over = (close - lvl) / atr
    return {"domain": "repair", "trigger": lvl + FASTLANE_K * atr, "over_n": over,
            "first_day": streak_n == 1, "mag_ok": over > FASTLANE_K,
            "vol_ok": vol_ok, "vol_note": vol_note}


def fastlane_lines(fl: Optional[dict], g: dict, need: int, streak: List[str],
                   close: float) -> List[str]:
    """⏳ 行下面那两行展开 (用户  选定的版式)。"""
    if not fl:
        return []
    if fl.get("domain") != "repair":                       # ⓪ 域外/不可判
        c = fl.get("domain")
        if c is None:
            return ["     ⚡域不可判(序列不足) — 保守 ×2 (与 live _fastlane 同判)"]
        return [f"     ⚡域外({PATH_TAGS.get(c, c)}) — 非修复路径无快车道, "
                f"×{need} 恒定 (gate_path_taxonomy  四类裁决)"]
    lvl, out = g["level"], []
    if not fl["first_day"]:
        return [f"     ⚡快车道不适用: 首日已过 ({streak[0][5:] if streak else '?'})"
                f" — 只能 ×{need}, 还需 {need - len(streak)} 个收盘"]
    if fl["mag_ok"] and fl["vol_ok"]:
        # 报表跑在 postclose 之前时会看到这个 —— 转移在途, 不是"没生效"
        return [f"     ⚡快车道达标: 首日超 {fl['over_n']:.2f}N ≥ {FASTLANE_K}N"
                + (f", {vol_short(fl['vol_note'], True)}" if fl["vol_note"] else "")
                + " — 当日转移在途 (等 postclose 的 regime 步)"]
    why = []
    if not fl["mag_ok"]:
        why.append(f"幅度 {fl['over_n']:.2f}N < {FASTLANE_K}N "
                   f"(差 ${fl['trigger'] - close:,.2f}, 需收 ≥{fl['trigger']:,.2f})")
    if not fl["vol_ok"]:
        why.append(f"{vol_short(fl['vol_note'], False)} < {g.get('vol_min'):g}")
    elif fl["vol_note"]:
        why.append(vol_short(fl["vol_note"], True))
    out.append(f"     ⚡快车道未成: 首日 {streak[0][5:] if streak else '?'} " + "; ".join(why))
    out.append(f"     → 首日已消耗 — 本轮只剩 ×{need}: 下个收盘 >{lvl:g} 即成立 (跌回则重置)")
    return out


def render_ticker(tk: str, agent: dict, bars: List[dict], held: bool = False,
                  deep: Optional[List[dict]] = None,
                  deep_note: Optional[str] = None) -> List[str]:
    if len(bars) < 2:
        return [f"{tk}: 无缓存 K 线 — 先跑 kline_cache"]
    close = coerce_float(bars[-1].get("close"))
    prev = coerce_float(bars[-2].get("close"))
    chg = (close / prev - 1) * 100 if close and prev else 0.0
    vr = vol_ratio_last(bars)
    vr_s = f"{vr:.2f}×{'⚡' if vr >= VOL_GATE else ''}" if vr is not None else "?"
    L = [f"{tk} ${close:g} ({chg:+.1f}%) 量比 {vr_s}"]
    # 线名 = gate 的**结构身份**四分类 (换掉旧的 trend/lsh 两档启发式)。
    # 修复路径仍用 止跌/收复/修复 三词; 非修复路径分 突破线/箱体顶/新高线。
    st = technical.swing_structure(bars)
    # 🧭 结构 + 动态 lsh (v3 ⑤): 修复线权威读数与结构标签同行, 每票都渲染
    lsh = coerce_float(st.get("last_swing_high"))
    if st.get("trend"):
        s = f"  🧭 {st.get('high_label')}+{st.get('low_label')}({st.get('trend')})"
        if lsh and close:
            s += f" | 动态 lsh {lsh:g} ({(lsh / close - 1) * 100:+.1f}%)"
        L.append(s)

    zones = agent.get("zones") or {}
    z1 = zones.get("accumulation_zone_1")
    trim = zones.get("trim_zone")
    nc = coerce_float(zones.get("no_chase_above"))
    if z1 and close is not None:
        # 位置行全覆盖 (用户: 持仓常态在 z1 上方, 旧版只画带内/带下 →
        # NBIS 整块只剩一条 gate 行, "持仓 section 反而最没数据")
        if z1[0] <= close <= z1[1]:
            L.append(f"  📍 zone1 {z1[0]:g}-{z1[1]:g} 带内 (左侧参与位)")
        elif close < z1[0]:
            L.append(f"  📍 zone1 {z1[0]:g}-{z1[1]:g} 之下 — 对照 stop {zones.get('stop_level')}")
        elif trim and close > trim[1]:
            L.append(f"  📍 trim {trim[0]:g}-{trim[1]:g} 上方 (+{(close / trim[1] - 1) * 100:.1f}%)"
                     " — breakout-stale 观察")
        elif trim and trim[0] <= close <= trim[1]:
            L.append(f"  📍 trim {trim[0]:g}-{trim[1]:g} 带内"
                     + (" (持仓减仓观察)" if held else " — 不做入场"))
        elif nc and close > nc:
            L.append(f"  📍 no_chase {nc:g} 上方 (+{(close / nc - 1) * 100:.1f}%) — 不追价")
        else:
            L.append(f"  📍 等待区: zone1 上沿 {(close / z1[1] - 1) * 100:+.1f}%"
                     + (f" | no_chase {nc:g}" if nc else ""))
    if held and coerce_float(zones.get("stop_level")) is not None:
        L.append(f"  🛑 stop {zones['stop_level']:g} (收盘 ×{zones.get('stop_level_days') or 1}日)")

    atr = (technical.atr(bars, ATR_PERIOD) or [None])[-1]
    if deep is None:                      # 调用方没给深序列 (render_ticker 保持无 IO)
        deep, deep_note = bars, deep_note or "未取深度序列"
    # 深度缓存**不入 git 且会过期**—— 降级必须说出口, 不许静默拿一年的高点当 ATH
    if any(g["kind"] in RETAG_KINDS for g in ladder_of(agent)):
        if len(deep) < MIN_BARS:
            L.append(f"  ⚠️ 线名回落旧规则: 序列 n={len(deep)} < {MIN_BARS}"
                     + (f" ({deep_note})" if deep_note else ""))
        elif deep_note:
            L.append(f"  ⚠️ 线名按现有序列判: {deep_note} (n={len(deep)})")
    for g in ladder_of(agent):
        cls = gate_path_of(g["level"], deep, st.get("trend"), atr)
        g = retag(g, cls, lsh, uptrend=st.get("trend") == "up")
        streak = _gate_streak(bars, g["level"])
        n, need = len(streak), g["days"]
        dist = gate_distance(close, atr, g["level"])
        # 线名与 ⚡域共用同一个 cls —— 一次计算, 两处消费, 永不分叉
        fl = fastlane_state(bars, close, atr, g, n, tk, cls=cls)
        if close is not None and close <= g["level"]:
            line = f"  ▫️ {g['level']:g} {g['tag']} [0/{need}] {dist}"
            if fl and fl.get("domain") == "repair":
                # 下一个收盘就是首日 → 预告两个价: gate = 开始 ×2 计数;
                # gate+0.5N = 当日转移免确认。量腿读数是**今日**的, 不预测明日。
                seg = f" | ⚡ ≥{fl['trigger']:,.2f} 免 ×{need}"
                if g.get("vol_min"):
                    seg += f" (今日 {vol_short(fl['vol_note'], fl['vol_ok'])})"
                line += seg
            elif fl:
                # ⓪域外/不可判: live _fastlane 不会发车 —— 预告「⚡ ≥X 免 ×2」
                # 就是预告一条不存在的路 (接线, 与转移机同判)
                c = fl.get("domain")
                line += (" | ⚡域不可判 — ×%d 恒定" % need if c is None
                         else f" | ⚡域外({PATH_TAGS.get(c, c)}) — ×{need} 恒定")
            L.append(line)
        elif n >= need:
            line = (f"  ✅ {g['level']:g} {g['tag']} [{min(n, need)}/{need} 成立]"
                    + (f" 自 {streak[0]}" if streak else "") + f" · {dist}")
            # 成立即可挂 ladder → 直接给风险读数。地板 = 卡片 next_regime 自己写的
            # stop, 没写则按 round-2b 约定 gate−0.5N 推; 来源标出来, 不混。
            if g.get("kind") == "entry" and atr:
                ns, src = g.get("next_stop"), "卡"
                if ns is None:
                    ns, src = g["level"] - BFLOOR_BUF * atr, "约定"
                line += f" → stop {ns:,.2f}[{src}] 风险 {(close - ns) / atr:.2f}N"
            # 卡片自带的量能闸: regime_transition.volume_leg 同一函数 (起
            # 相对口径, 裁决 R) — 报表与转移机永不分叉。收盘 streak 与量能闸是两个
            # 事实, 只报 ✅ 会把"差量能"的 gate 播成全过。
            vm = g.get("vol_min")
            if vm:
                vok, vnote = volume_leg(bars, vm, tk)
                if not vok:
                    line += f" · ⚠️ {vnote} < {vm:g} 量能闸未过"
            L.append(line)
        else:
            L.append(f"  ⏳ {g['level']:g} {g['tag']} [{n}/{need}] {dist}")
            L.extend(fastlane_lines(fl, g, need, streak, close))

    # 🔒 约束播报 = sizing.constraint_lines (canonical)。窗口态以本报表的快照日
    # (最后完成 bar) 为准, 与"截至 X 收盘"同框 —— 手搓渲染丢 from 的病  修。
    asof = str(bars[-1].get("time", ""))[:10]
    for ln in constraint_lines(read_constraints(agent), asof):
        L.append("  " + ln)
    return L


def skhy_premium_line(market_dir: Path = MARKET_DIR) -> Optional[str]:
    p = market_dir / "premium" / "SKHY.jsonl"
    if not p.exists():
        return None
    try:
        last = json.loads(p.read_text().strip().rsplit("\n", 1)[-1])
        prem = coerce_float(last.get("premium_pct"))
        if prem is None:
            return None
        gate = "未过 (>20% → 双闸 clamp)" if prem > 20 else "✅ 已压缩至阈内"
        return f"  💱 ADR 溢价 {prem:+.1f}% ({str(last.get('date') or '')[:10]}) — premium gate {gate}"
    except (ValueError, IndexError):
        return None


def build_report(wl_path: Path = WATCHLIST_PATH, kdir: Path = KLINE_CACHE_DIR,
                 market_dir: Path = MARKET_DIR, pos_path: Path = POSITIONS_PATH) -> str:
    wl = json.loads(Path(wl_path).read_text())["tickers"]
    # volume_reference 票 (SPY/QQQ/SMH/SOXX) 不出 ticker 块 — 读数已在 🏭 板块表头
    active = {tk: c for tk, c in wl.items()
              if c.get("moomoo_listed", True)
              and (c.get("instrument") or {}).get("role") != "volume_reference"}
    # 持仓侧 (用户: 持仓卡同样带 gate 阶梯, 只报 watchlist 是覆盖缺口)。
    # 判据 = holdings.live_tickers (broker.qty 单一真源); 结构豁免层无 gate 不渲染。
    held: dict = {}
    try:
        pos = json.loads(Path(pos_path).read_text())
        tickers = pos.get("tickers") or {}
        for tk in sorted(live_tickers(pos)):
            card = tickers.get(tk) or {}
            if not is_managed(card):
                continue
            held[tk] = card
    except (OSError, ValueError):
        pass  # positions 读不到只缺持仓 section, watchlist 半仍照发
    dates = set()

    def _blocks(cards: dict, held_flag: bool = False) -> List[str]:
        out = []
        for tk in sorted(cards):
            agent = cards[tk].get("agent") or {}
            bars = load_bars(tk, kdir)
            if bars:
                dates.add(str(bars[-1].get("time", ""))[:10])
            dbars, dnote = deep_bars(tk, kdir)
            block = render_ticker(tk, agent, bars, held=held_flag,
                                  deep=dbars, deep_note=dnote)
            if tk == "SKHY":
                pl = skhy_premium_line(market_dir)
                if pl:
                    block.append(pl)
            out.append("\n".join(block))
        return out

    held_blocks = _blocks(held, held_flag=True)
    wl_blocks = _blocks({tk: c for tk, c in active.items() if tk not in held})
    asof = max(dates) if dates else "?"
    head = (f"📐 Gate 阶梯收盘报 — 截至 {asof} 收盘 (完成 bar)\n"
            f"✅=站稳成立 ⏳=计数中 ▫️=未触及 | 量比⚡=≥{VOL_GATE} (完成日口径)\n"
            f"距离双单位 %(N): 同一个 % 在不同票上差好几倍, N 才可比\n"
            f"⚡快车道 = 首日收盘 > gate+{FASTLANE_K}N 且量腿过 → **当日转移免确认**\n"
            f"  (ladder_entry  采纳; 只对 next_regime 那道线, 止跌/修复线无此路)\n"
            f"  增益带 0.5-1.0N; >1.0N 与等 ×2 平局 — 幅度不是越大越好 (§10c)\n"
            f"线名=结构层 (止跌/收复/修复/突破); 仓位档由结构判别式定\n"
            f"(v3 2026-08-04 纯结构单腿, thesis 仅 [FYI]), 不由单一 gate 解锁。\n"
            f"gate 成立 → regime_transition 机械转移 (两个桶都扫,  裁决 A);\n"
            f"  转移 ≠ 已授权: provisional 带补画锁, 补画前 entry_guard 不给 ladder\n")
    sec = sector_lines(kdir)
    if sec:
        head += "\n".join(sec) + "\n"
    parts = [head]
    if held_blocks:
        parts.append("📌 持仓\n\n" + "\n\n".join(held_blocks))
    parts.append("👀 Watchlist\n\n" + "\n\n".join(wl_blocks))
    return "\n".join(parts)


def main():
    text = build_report()
    if "--json" in sys.argv:
        print(json.dumps({"text": text}, ensure_ascii=False))
    else:
        print(text)


if __name__ == "__main__":
    main()
