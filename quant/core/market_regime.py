"""大盘状态机 market_regime —— C1「现在是什么行情 / 出现什么会变成什么」（判读层，零授权效力）。

════════════════════════════════════════════════════════════
为什么存在
════════════════════════════════════════════════════════════

  回答两件事：目前是震荡行情还是单边趋势行情；未来出现哪些信息确认会使行情发生怎样的转变。

════════════════════════════════════════════════════════════
判别式（全部收盘口径；只吃已完成日线）
════════════════════════════════════════════════════════════

  ER20     效率比 = |C_t − C_{t−20}| ÷ Σ_{20} |ΔC|。1 = 一条直线，→0 = 来回拉锯。一个参数（20）。
  分位     当日 ER20 在**自身之前 252 根** ER20 里的分位（严格小于当日值的比例）。阈值是自身分位，
           不是拟合常数：下三分位 = 震荡档，上三分位 = 单边档，中间 = 不明档。
  方向     `core/dow.py` 短期道氏状态机（零数值参数）：trend up/down，phase 0 = 推进，≥1 = 观察期。
  状态     震荡档                      → 震荡（与方向无关：来回拉锯就是拉锯）
           单边档 ∧ 道氏推进 ∧ 上涨    → 单边上行
           单边档 ∧ 道氏推进 ∧ 下跌    → 单边下行
           其余（不明档 / 道氏观察期）   → 转换中
           样本不足（ER 分位或道氏方向缺） → 未定

  「预测」的口径 = 条件路径 + 历史频率，不给点位：
    - 价位转移：道氏机给出的下一根线（`dow.describe`）；
    - 档位转移：ER20 升到自身 p67 以上 = 单边档 / 降到 p33 以下 = 震荡档（阈值随窗口滚动，给数字）；
    - 历史频率：同一标的历史上处于同一状态时，h 根之后各状态的频率（`transition_rates`）——
      只是频率，不是概率；ledger 对账之前没有校准。

🚨 零授权：本模块只被判读层（chart_read / 简报一行）与研究层消费；不改 PLAN / VETO / cap / 股数。
只 import core。无前视：第 t 根只用 bars[≤t]（ER 与分位都是尾窗；道氏机自证无前视）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import dow

ER_WINDOW = 20
PCTL_WINDOW = 252
BAND_LOW = 1 / 3            # 分位 < 1/3 = 震荡档
BAND_HIGH = 2 / 3           # 分位 ≥ 2/3 = 单边档
BENCHMARKS = ("SPY", "QQQ", "SMH")

RANGE, TREND_UP, TREND_DOWN, TRANSITION, UNKNOWN = "range", "trend_up", "trend_down", "transition", "unknown"
STATES = (TREND_UP, TREND_DOWN, RANGE, TRANSITION)
STATE_CN = {TREND_UP: "单边上行", TREND_DOWN: "单边下行", RANGE: "震荡",
            TRANSITION: "转换中", UNKNOWN: "未定"}
BAND_CN = {"range": "震荡档", "mixed": "不明档", "trend": "单边档"}


def _close(bar: dict) -> float:
    return float(bar["close"])


def _date(bar: dict) -> str:
    return str(bar.get("date") or bar.get("time") or "")[:10]


def er_series(closes: Sequence[float], n: int = ER_WINDOW) -> List[Optional[float]]:
    """逐根 ER_n；前 n 根与零波动窗口 = None。"""
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= n:
        return out
    absd = [0.0] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    path = sum(absd[1:n + 1])
    for t in range(n, len(closes)):
        if t > n:
            path += absd[t] - absd[t - n]
        out[t] = (abs(closes[t] - closes[t - n]) / path) if path > 1e-12 else None
    return out


def pctl_series(values: Sequence[Optional[float]], window: int = PCTL_WINDOW) -> List[Optional[float]]:
    """当日值在自身之前 `window` 个有效值里的分位（严格小于的比例）；前序不足 `window` = None。"""
    out: List[Optional[float]] = [None] * len(values)
    hist: List[float] = []
    for t, v in enumerate(values):
        if v is not None and len(hist) >= window:
            prior = hist[-window:]
            out[t] = sum(1 for x in prior if x < v) / window
        if v is not None:
            hist.append(v)
    return out


def band_of(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    if p < BAND_LOW:
        return "range"
    if p >= BAND_HIGH:
        return "trend"
    return "mixed"


def classify(band: Optional[str], trend: Optional[str], phase: int) -> str:
    if band is None or trend is None:
        return UNKNOWN
    if band == "range":
        return RANGE
    if band == "trend" and phase == 0:
        return TREND_UP if trend == "up" else TREND_DOWN
    return TRANSITION


def timeline(bars: List[dict]) -> List[Dict]:
    """全序列逐根状态（回测 / 历史频率用）。每根 {date, er, pctl, band, trend, phase, state}。"""
    closes = [_close(b) for b in bars]
    er = er_series(closes)
    pc = pctl_series(er)
    tl = dow.replay(bars)["timeline"] if len(bars) >= 3 else [(None, 0)] * len(bars)
    out = []
    for t, b in enumerate(bars):
        trend, phase = tl[t]
        band = band_of(pc[t])
        out.append({"date": _date(b), "er": er[t], "pctl": pc[t], "band": band,
                    "trend": trend, "phase": phase, "state": classify(band, trend, phase)})
    return out


def transition_rates(states: Sequence[str], current: str, horizon: int) -> Optional[Dict]:
    """历史上处于 `current` 的日子，`horizon` 根之后各状态的频率。n = 样本日数（重叠窗口，不是独立样本）。"""
    idx = [t for t, s in enumerate(states[:-horizon] if horizon else states) if s == current]
    if not idx:
        return None
    cnt: Dict[str, int] = {}
    for t in idx:
        s = states[t + horizon]
        if s == UNKNOWN:
            continue
        cnt[s] = cnt.get(s, 0) + 1
    n = sum(cnt.values())
    if not n:
        return None
    return {"n": n, "horizon": horizon, "rates": {s: cnt.get(s, 0) / n for s in STATES}}


def _quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def state(bars: List[dict], horizons: Sequence[int] = (5, 20)) -> Dict:
    """当前状态卡：状态 + ER 档 + 道氏阶段 + 转移条件 + 历史频率。只吃已完成日线。"""
    if not bars:
        return {"state": UNKNOWN, "state_cn": STATE_CN[UNKNOWN]}
    tl = timeline(bars)
    cur = tl[-1]
    ds = dow.structure_state(bars)
    er_hist = [x["er"] for x in tl[:-1] if x["er"] is not None][-PCTL_WINDOW:]
    thresholds = ({"range_below": _quantile(er_hist, BAND_LOW), "trend_at": _quantile(er_hist, BAND_HIGH)}
                  if len(er_hist) >= PCTL_WINDOW else None)
    states = [x["state"] for x in tl]
    return {
        "date": cur["date"], "close": _close(bars[-1]),
        "state": cur["state"], "state_cn": STATE_CN[cur["state"]],
        "er": cur["er"], "pctl": cur["pctl"], "band": cur["band"],
        "band_cn": BAND_CN.get(cur["band"] or "", "样本不足"),
        "er_thresholds": thresholds,
        "dow": {"trend": ds.get("trend"), "phase": ds.get("phase"), "stage_cn": ds.get("stage_cn"),
                "lines": dow.describe(ds)[1:]},
        "rates": {h: transition_rates(states, cur["state"], h) for h in horizons}
        if cur["state"] != UNKNOWN else {},
    }


def describe(card: Dict) -> List[str]:
    """机械白话：一行状态 + 转移条件 + 历史频率（频率不是概率）。"""
    if card.get("state") in (None, UNKNOWN) and not card.get("dow"):
        return [f"{STATE_CN[UNKNOWN]}（样本不足）"]
    er = card.get("er")
    p = card.get("pctl")
    head = (f"{card['state_cn']} · ER20 {er:.2f}（自身 252 日分位 {p:.0%} = {card['band_cn']}）"
            if er is not None and p is not None else f"{card['state_cn']} · ER20 样本不足")
    L = [head, f"道氏：{card['dow']['stage_cn']}"]
    L += [f"  {x}" for x in card["dow"]["lines"]]
    th = card.get("er_thresholds")
    if th and th.get("range_below") is not None:
        L.append(f"档位线：ER20 ≥ {th['trend_at']:.2f} = 单边档；< {th['range_below']:.2f} = 震荡档")
    for h, r in sorted((card.get("rates") or {}).items()):
        if not r:
            continue
        parts = " / ".join(f"{STATE_CN[s]} {r['rates'][s]:.0%}" for s in STATES if r["rates"].get(s))
        L.append(f"历史频率（同状态 {h} 根后，n={r['n']} 日，重叠窗口）：{parts}")
    return L
