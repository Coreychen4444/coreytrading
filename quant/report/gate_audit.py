#!/usr/bin/env python3
"""卡片 gate 合规审计 —— 把 `.claude/rules/zone-maintenance.md §Gate = 状态转移轴`
的 10 条制定标准里**机械可查的六条**变成会失败的东西。

为什么存在 (): 用户 定版「gate = 状态转移轴, 三种趋势都要有, 画卡时
同步给出」并称其为整个系统的根基。而这个仓库反复栽在同一处 —— **规则不进代码
= 不存在**。当天全池审计手工跑出 9 张不合规 (5 张缺 preview / 1 张 stop 压在
买带之上), 全部是「文档里写着、没有任何东西检查」的形状。

只查机械可判的:
  ③ 确认口径      entry_gate 要有 days 与 volume_ratio_min
  ④ 自洽          上行 gate ≥ 本 regime no_chase (== 正常, < 倒挂)
  ⑤ 单调          stop < z1_lo / z1_hi ≤ no_chase / no_chase ≤ trim_lo
  ⑥ 地板缓冲      下一档 stop 距 gate > 0.25×ATR20 (贴 gate = whipsaw 语义)
  ⑧ preview       缺了 apply_transition 会写 next_regime=None (缺口 2)
  ⑨ 锚留痕        entry_gate.anchored_on.basis + zones.anchored_on.date

**不查**需要人读 level_map 的四条 (①锚是不是 tape 地标 ②取簇哪个沿
⑦provisional 是否恰当 ⑩真空里有没有放追价上限) —— 那些是判断, 不是对账。

⚠️ **`trim` 与 `gate` 之间没有序关系约束。**立表时曾臆断
`trim_hi ≤ gate`, 同日全池审计推翻: 12 张卡三种排列都在用 (gate 低于/等于/
高于 trim)。两者不在同一个时点生效 —— trim 管当前 regime 的止盈, gate 一旦
×N 日确认整张图连同 trim 一起被替换。**不要把它加回来。**
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import json
from typing import Any, Dict, List, Optional

from quant.core.holdings import is_managed
from quant.core.io_json import iter_jsonl
from quant.core.num import coerce_float
from quant.core.paths import KLINE_CACHE_DIR, POSITIONS_PATH, WATCHLIST_PATH
from quant.core.structure import has_next_gate
from quant.core.technical import atr, swing_structure

FLOOR_GAP_ATR = 0.25   # 同 entry_guard.floor_gate_gap_lines —— 地板贴 gate 的判据


def _atr20(ticker: str, kdir: Path = KLINE_CACHE_DIR) -> Optional[float]:
    rows = list(iter_jsonl(kdir / f"{ticker}.jsonl"))
    if len(rows) < 25:
        return None
    v = atr(rows, 20)
    return v[-1] if isinstance(v, list) else v


def _swing_ctx(ticker: str, kdir: Path = KLINE_CACHE_DIR):
    """→ (道氏结构, 最后收盘)。kline 不足 → (None, None), 调用方跳过①。"""
    rows = list(iter_jsonl(kdir / f"{ticker}.jsonl"))
    if len(rows) < 60:
        return None, None
    return swing_structure(rows), coerce_float(rows[-1].get("close"))


def auditable_cards(*docs: Optional[dict]):
    """→ [(ticker, agent)]。活跃 · 正股 · 受管 · 有 zones —— 其余不在本审计辖区。

    期权卡的转移轴在 underlying 上 (同 current_regime 的期权豁免); 结构豁免层
    (`commemorative_hold`) 整个不进任何 task 输出 (position-tiers.md)。
    """
    out = []
    for doc in docs:
        for t, body in sorted(((doc or {}).get("tickers") or {}).items()):
            if body.get("status") == "closed" or not is_managed(body):
                continue
            if ((body.get("instrument") or {}).get("type")) == "option":
                continue
            agent = body.get("agent") or {}
            if agent.get("zones"):
                out.append((t, agent))
    return out


def audit_card(ticker: str, agent: dict, *,
               atr20: Optional[float] = None) -> List[str]:
    """一张卡的违规清单 (空 = 合规)。`atr20=None` ⇒ 跳过⑥ (算不出不等于不合规)。"""
    z = agent.get("zones") or {}
    nr = agent.get("next_regime") or {}
    g = nr.get("entry_gate") or {}
    lvl = coerce_float(g.get("level"))
    nc = coerce_float(z.get("no_chase_above"))
    out: List[str] = []

    # ⑧ 上行转移轴本身 —— 没有它, 后面几条都无从谈起
    if not has_next_gate(agent):
        cap = coerce_float((agent.get("current_regime") or {}).get("cap_pct_nav")) or 0
        out.append("⑧ 无 next_regime.entry_gate — "
                   + ("只有一轮 (R2/R3 无授权路径)" if cap > 0
                      else "cap 0 且无上行出口 = 锁死, 只能等破位"))
        return out

    # ③ 确认口径
    if not g.get("days") or g.get("volume_ratio_min") is None:
        out.append(f"③ 确认口径不全: days={g.get('days')} "
                   f"volume_ratio_min={g.get('volume_ratio_min')}")

    # ④ 自洽 (== 是正常设计: 在追价上限处确认突破)
    if lvl is not None and nc is not None and lvl < nc:
        out.append(f"④ 倒挂: 上行 gate {lvl:g} < no_chase {nc:g} —— "
                   "gate 触发时还在「可以追」的区间里")

    # ⑤ 单调 (trim_hi vs gate 无约束, 见模块 docstring)
    z1, tz, stop = z.get("accumulation_zone_1"), z.get("trim_zone"), z.get("stop_level")
    stop = coerce_float(stop)
    if z1 and stop is not None and stop >= coerce_float(z1[0]):
        out.append(f"⑤ stop {stop:g} ≥ z1 下沿 {z1[0]:g} —— 买带压在止损线之下")
    if z1 and nc is not None and coerce_float(z1[1]) > nc:
        out.append(f"⑤ z1 上沿 {z1[1]:g} > no_chase {nc:g}")
    if tz and nc is not None and nc > coerce_float(tz[0]):
        out.append(f"⑤ no_chase {nc:g} > trim 下沿 {tz[0]:g} —— 追进自己的减仓区")

    # ⑥ 地板缓冲
    ns = coerce_float((nr.get("zones") or {}).get("stop_level"))
    if lvl is not None and ns is not None and atr20:
        gap = lvl - ns
        if gap <= FLOOR_GAP_ATR * atr20:
            out.append(f"⑥ 下一档地板贴 gate: 距 {gap:.2f} ≤ "
                       f"{FLOOR_GAP_ATR}N {FLOOR_GAP_ATR * atr20:.2f} = whipsaw 语义")

    # ⑨ 锚留痕 —— 没有它「证据标准不得放宽」就只是一句话
    if not (g.get("anchored_on") or {}).get("basis"):
        out.append("⑨ entry_gate 无 anchored_on.basis (证据未留痕)")
    if not (z.get("anchored_on") or {}).get("date"):
        out.append("⑨ zones 无 anchored_on.date")

    return out


SWING_SKIP_ATR = 0.5   # 同 bfloor_buf 的缓冲宽度 —— 一个确认缓冲之内不算跳过


def advise_card(ticker: str, agent: dict, *, swing: Optional[dict],
                atr20: Optional[float], spot: Optional[float]) -> List[str]:
    """①的**第一个机械化**: gate 有没有跳过更近的 last swing high。

     源案例 META: 我拿继承来的 `no_chase 602` 当固定值, 再按标准④
    (gate ≥ no_chase) 去它**上面**找第一个簇 → 画出 628.5。而 601 一直在那里:
    道氏 `last_swing_high` 恰好 601.0、pivot x6 [high+low] 簇上沿恰好 601.00、
    MA50 600、拒 18、近 12 日盘中冲过四次全部收在其下。
    **因果被我倒过来了 —— 让一条旧线决定了 gate 能画在哪。**

    机械判据: `spot < lsh` 且 `gate > lsh + 0.5×ATR20` ⇒ 这条 gate 从现价走到
    它, 中途要先越过一个尚未被收复的摆动高点 —— **值得复核**, 不是判决。

    🚨 **lsh 只是一个维度, 不是 gate 的定义**(用户  纠正: 「gate 是
    一个多维度市场确认价位」)。META 601 之所以对, 不是因为它是 lsh, 是因为
    道氏 lsh + x6[H+L] 簇上沿 + MA50 + 拒18 + 四次冲高不收**五维落在同一个数**。
    本函数能抓「跳过了一个尚未收复的摆动高点」, **抓不到**「这个数只有一维支撑」——
    后者仍归人眼与 `anchor_receipts` 的收据。别把这条检查当成①族的全部。

    为什么是 advisory 不是 error: **gate 置于 lsh 之上一点是正确做法**——
    「收上」意味着收在其上, 恰好等于 lsh 会有歧义。实测量级自己会说话:
    MRVL 0.02N / NBIS 0.06N / MU 0.21N 是正常置法, META 1.21N 是真跳过。
    缓冲取 0.5N = 与 `bfloor_buf` 同一个house常数, 不另拍一个。
    """
    if not (swing and atr20 and spot):
        return []
    if not has_next_gate(agent):
        return []
    lvl = coerce_float(((agent.get("next_regime") or {})
                        .get("entry_gate") or {}).get("level"))
    lsh = coerce_float(swing.get("last_swing_high"))
    if lvl is None or lsh is None or spot >= lsh:
        return []
    over = lvl - lsh
    if over <= SWING_SKIP_ATR * atr20:
        return []
    return [f"① gate {lvl:g} 越过 last_swing_high {lsh:g} 达 {over / atr20:.2f}N "
            f"(现价 {spot:g} 仍在其下) —— 道氏意义上收上 lsh 本身就是状态转移, "
            f"复核 lsh 是不是第一条轴、当前这条是不是第二条"]


def run(*docs: Optional[dict], atr_of=None, ctx_of=None) -> List[Dict[str, Any]]:
    """→ [{ticker, issues, advisories}]，只含有问题的卡。

    `issues` = 硬违规 (机械可判); `advisories` = 需要人复核的疑点 (①族)。
    """
    atr_of = atr_of or _atr20
    ctx_of = ctx_of or _swing_ctx
    findings = []
    for t, agent in auditable_cards(*docs):
        n = atr_of(t)
        swing, spot = ctx_of(t)
        issues = audit_card(t, agent, atr20=n)
        adv = advise_card(t, agent, swing=swing, atr20=n, spot=spot)
        if issues or adv:
            findings.append({"ticker": t, "issues": issues, "advisories": adv})
    return findings


def main() -> int:
    want_json = "--json" in sys.argv
    docs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (POSITIONS_PATH, WATCHLIST_PATH) if p.exists()]
    total = len(auditable_cards(*docs))
    findings = run(*docs)
    if want_json:
        print(json.dumps({"cards": total, "findings": findings},
                         ensure_ascii=False, indent=2))
        return 0
    hard = [f for f in findings if f["issues"]]
    soft = [f for f in findings if f["advisories"]]
    print(f"卡 {total} 张 | 硬违规 {len(hard)} | 待复核 {len(soft)}"
          f"   (标准见 .claude/rules/zone-maintenance.md §Gate = 状态转移轴)\n")
    for f in hard:
        print(f"❌ {f['ticker']}")
        for i in f["issues"]:
            print(f"     {i}")
    for f in soft:
        print(f"🔎 {f['ticker']}  (advisory — 需要人复核, 不是判决)")
        for i in f["advisories"]:
            print(f"     {i}")
    if not findings:
        print("✅ 机械标准全部合规, ①族无疑点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
