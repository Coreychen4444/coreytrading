#!/usr/bin/env python3
"""量族冗余体检 —— OBV / A-D Line / CMF / MFI **vs**我们已在用的 RelVR。

**只算相关与提升度，不算任何收益。**与 `indicator_redundancy.py` 同一条纪律：
看见收益就会忍不住调阈值，而那正是 Sullivan-Timmermann-White / Bajgrowicz-Scaillet
（7,846 条规则 FDR 后无一存活）要防的。本脚本里**没有前向收益计算**，是刻意的。

## 它回答什么（`technical_engine_landing_plan.md` §5 阶段 7D 的唯一前置）

syllabus 第七阶段 D 族列了 12 个量能指标。landing plan 把它标成
**「唯一可能有正交增量的一族」**—— 理由是 Neely 等 (2014) 的 14 个指标里有 6 个是 OBV 类，
而我们现在只有 **RelVR**（当日相对量）这一个量读数。

但那是**猜测**，本脚本把它变成数字，回答三问：

  Q1  量族内部彼此独立吗？（4 个指标 → 有效维数几维）
  Q2  量族 = RelVR 的另一种写法吗？（若是 ⇒ 阶段 7D 整族讨论当场关掉）
  Q3  🚩 **量族其实是价格的影子吗？**—— OBV 与 A/D 都是**带符号累积**，
      其斜率在构造上就与价格方向纠缠。若它们与 `mom20` / `ma20_dev` 的相关
      高于与 RelVR 的相关，那它们**根本不在轴 B（力），而在轴 B′（果）**，
      于是「加一个量能指标」实际上是**又加了一票价格**。

**它不能回答**「有没有增量预测价值」—— 那要 T3 的预注册回测。
高相关 ⇒ 可以关掉讨论；低相关 **⇏**有 alpha。（landing plan §13 诚实边界第 7 条）

## 实现约定

- 四个候选指标**就地实现在本文件里**，**不进 `core/`**—— 它们尚未被采纳，
  `core/` 是"单一真源"不是"素材库"。若将来采纳，再搬 + 补镜像测试。
- RelVR **不重新实现**：`volume_ratio` 的数学、`sectors.benchmark_for` 的冻结表、
  `regime_transition.REL_FLOOR` 的常数全部 import 既有真源，
  **只有数据源不同**（本脚本读 `klines_deep` 深度池，live 量腿读 13 个月常规缓存）。
- 池 = `klines_deep`（去相关扩容池），不是 live 的 32 票 ——
  那是「几乎纯 AI/半导体、单个宏观周期」。

Usage:
    .venv/moomoo/bin/python3 quant/research/volume_family_redundancy.py --limit 60 [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
from typing import List, Optional  # noqa: E402

import numpy as np  # noqa: E402

from quant.core import technical as ta  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR  # noqa: E402
from quant.core.sectors import benchmark_for  # noqa: E402
from quant.decision.regime_transition import REL_FLOOR  # noqa: E402
from quant.research.indicator_redundancy import load_bars, participation_ratio  # noqa: E402

VOL_WIN = 20        # OBV / A-D 斜率窗口 + CMF 窗口（与 volume_ratio 的 20 同拍）
MFI_WIN = 14        # MFI 传统窗口（与 ATR14 的"读盘"口径同拍）


# ── 四个候选指标（就地实现，未采纳，不进 core/） ──────────────────

def obv(bars: List[dict]) -> List[float]:
    """On-Balance Volume：收涨记 +vol，收跌记 −vol，平记 0，累积。"""
    out, cum = [], 0.0
    prev: Optional[float] = None
    for b in bars:
        c, v = float(b["close"]), float(b.get("volume") or 0)
        if prev is not None:
            cum += v if c > prev else (-v if c < prev else 0.0)
        out.append(cum)
        prev = c
    return out


def ad_line(bars: List[dict]) -> List[float]:
    """Accumulation/Distribution Line：Money Flow Multiplier × volume 的累积。
    MFM = ((C−L) − (H−C)) / (H−L)，即 `close_pos` 的 [−1,1] 版本。"""
    out, cum = [], 0.0
    for b in bars:
        h, low, c = float(b["high"]), float(b["low"]), float(b["close"])
        v = float(b.get("volume") or 0)
        rng = h - low
        cum += (((c - low) - (h - c)) / rng * v) if rng > 0 else 0.0
        out.append(cum)
    return out


def cmf(bars: List[dict], n: int = VOL_WIN) -> List[Optional[float]]:
    """Chaikin Money Flow = Σ(MFM×vol, n) / Σ(vol, n)，落在 [−1, 1]。

    🚩 **CMF(n) 与「A/D 线的 n 日斜率÷均量」在代数上是同一个式子**：
        ad_slope = Σ(MFM·v)/(n·avg_v)，而 n·avg_v = Σv  ⇒  ad_slope ≡ Σ(MFM·v)/Σv = CMF(n)
    所以本脚本输出里 `ad_slope ~ cmf20 = +1.000` 是**构造恒等式，不是实证发现**
    —— 它只说明 syllabus 的 D 族列表里，两个不同名字的指标是同一条公式。
    保留两条是为了让这件事在输出里看得见（Zakamulin「换指标常常只是换权重」的一个实例）。
    """
    out: List[Optional[float]] = [None] * len(bars)
    mfv, vols = [], []
    for b in bars:
        h, low, c = float(b["high"]), float(b["low"]), float(b["close"])
        v = float(b.get("volume") or 0)
        rng = h - low
        mfv.append((((c - low) - (h - c)) / rng * v) if rng > 0 else 0.0)
        vols.append(v)
    for i in range(n - 1, len(bars)):
        sv = sum(vols[i - n + 1:i + 1])
        out[i] = (sum(mfv[i - n + 1:i + 1]) / sv) if sv > 0 else None
    return out


def mfi(bars: List[dict], n: int = MFI_WIN) -> List[Optional[float]]:
    """Money Flow Index：典型价加权的「量版 RSI」，落在 [0, 100]。"""
    out: List[Optional[float]] = [None] * len(bars)
    tp = [(float(b["high"]) + float(b["low"]) + float(b["close"])) / 3 for b in bars]
    rmf = [tp[i] * float(bars[i].get("volume") or 0) for i in range(len(bars))]
    for i in range(n, len(bars)):
        pos = sum(rmf[j] for j in range(i - n + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(rmf[j] for j in range(i - n + 1, i + 1) if tp[j] < tp[j - 1])
        out[i] = 100.0 if neg <= 0 else 100.0 - 100.0 / (1 + pos / neg)
    return out


# ── RelVR：不重新实现，只是换数据源 ──────────────────────────────

_BENCH_CACHE: dict[str, dict[str, float]] = {}


def bench_ratio_map(bench: str) -> dict[str, float]:
    """基准的 20 日量比（按日期索引）。数学 = `technical.volume_ratio`（既有真源），
    数据源 = `klines_deep`（live 量腿读的是 13 个月常规缓存 —— **唯一的差别**）。"""
    if bench not in _BENCH_CACHE:
        rows = load_bars(bench)
        vols = [int(r.get("volume") or 0) for r in rows]
        ratios = ta.volume_ratio(vols, 20)
        _BENCH_CACHE[bench] = {str(rows[j].get("time") or "")[:10]: ratios[j]
                               for j in range(len(rows)) if ratios[j] is not None}
    return _BENCH_CACHE[bench]


def features(ticker: str, bars: List[dict]) -> tuple[List[str], np.ndarray]:
    """一票一矩阵：5 个量读数 + 2 个价读数（Q3 的对照）。"""
    closes = [float(b["close"]) for b in bars]
    vols = [int(b.get("volume") or 0) for b in bars]
    vr = ta.volume_ratio(vols, 20)
    o, a = obv(bars), ad_line(bars)
    cm, mf = cmf(bars), mfi(bars)
    ma20 = ta.sma(closes, 20)
    bmap = bench_ratio_map(benchmark_for(ticker))
    names = ["relvr_log", "obv_slope", "ad_slope", "cmf20", "mfi14",
             "mom20", "ma20_dev"]
    rows = []
    for i in range(len(bars)):
        if i < 60 or None in (vr[i], cm[i], mf[i], ma20[i]):
            continue
        day = str(bars[i].get("time") or "")[:10]
        b = bmap.get(day)
        if b is None:
            continue
        relvr = vr[i] / max(b, REL_FLOOR)
        avg_v = sum(vols[i - VOL_WIN + 1:i + 1]) / VOL_WIN
        if avg_v <= 0:
            continue
        rows.append([
            float(np.log(max(relvr, 1e-3))),
            (o[i] - o[i - VOL_WIN]) / (VOL_WIN * avg_v),      # 归一化斜率, 无量纲
            (a[i] - a[i - VOL_WIN]) / (VOL_WIN * avg_v),
            cm[i],
            (mf[i] - 50.0) / 50.0,
            closes[i] / closes[i - 20] - 1.0,
            closes[i] / ma20[i] - 1.0,
        ])
    return names, np.array(rows) if rows else np.zeros((0, len(names)))


def run(universe: List[str]) -> dict:
    mats, used = [], []
    names: List[str] = []
    for t in universe:
        bars = load_bars(t)
        if len(bars) < 200:
            continue
        names, m = features(t, bars)
        if len(m) < 100:
            continue
        m = (m - m.mean(axis=0)) / (m.std(axis=0) + 1e-12)   # 逐票标准化后池化
        mats.append(m)
        used.append(t)
    if not mats:
        return {"error": "样本不足"}
    X = np.vstack(mats)
    C = np.corrcoef(X, rowvar=False)

    vol_idx = [names.index(k) for k in ("relvr_log", "obv_slope", "ad_slope", "cmf20", "mfi14")]
    fam_idx = [names.index(k) for k in ("obv_slope", "ad_slope", "cmf20", "mfi14")]
    px_idx = [names.index(k) for k in ("mom20", "ma20_dev")]

    def eff(idx):
        w = np.linalg.eigvalsh(np.corrcoef(X[:, idx], rowvar=False))
        return round(participation_ratio(np.clip(w, 0, None)), 2)

    pairs = sorted(
        ((names[i], names[j], round(float(C[i, j]), 3))
         for i in range(len(names)) for j in range(i + 1, len(names))),
        key=lambda p: -abs(p[2]))

    # Q3：每个量族指标，与 RelVR 的相关 vs 与价格读数的最大相关
    verdict = {}
    ri = names.index("relvr_log")
    for k in ("obv_slope", "ad_slope", "cmf20", "mfi14"):
        i = names.index(k)
        vs_rel = abs(float(C[i, ri]))
        vs_px = max(abs(float(C[i, j])) for j in px_idx)
        verdict[k] = {"vs_relvr": round(vs_rel, 3), "vs_price_max": round(vs_px, 3),
                      "closer_to": "价格(轴B′)" if vs_px > vs_rel else "量(轴B)"}

    return {
        "tickers": len(used), "rows": int(X.shape[0]), "names": names,
        "eff_dim_volume_family_only": eff(fam_idx),          # Q1
        "eff_dim_with_relvr": eff(vol_idx),                  # Q2
        "eff_dim_all": eff(list(range(len(names)))),
        "top_pairs": pairs[:10],
        "family_vs_price": verdict,                          # Q3
    }


def render(r: dict) -> str:
    if r.get("error"):
        return r["error"]
    L = ["量族冗余体检 —— **只算相关，不算收益**",
         f"池 {r['tickers']} 票 / {r['rows']} 行（klines_deep，逐票标准化后池化）", ""]
    L.append(f"Q1 量族内部有效维数（OBV/AD/CMF/MFI 四个）  = {r['eff_dim_volume_family_only']} / 4")
    L.append(f"Q2 量族 + RelVR 五个一起             = {r['eff_dim_with_relvr']} / 5")
    L.append(f"   七个读数全放一起（含 2 个价读数）    = {r['eff_dim_all']} / 7")
    L.append("")
    L.append("Q3 🚩 每个量族指标离谁更近（这是本体检最该看的一栏）：")
    for k, v in r["family_vs_price"].items():
        L.append(f"   {k:10} |ρ| vs RelVR {v['vs_relvr']:.3f}　vs 价格读数(max) "
                 f"{v['vs_price_max']:.3f}　→ **更像{v['closer_to']}**")
    L.append("")
    L.append("最相关的对：")
    for a, b, c in r["top_pairs"]:
        note = ("  ← 构造恒等式，不是发现（见 cmf() docstring）"
                if {a, b} == {"ad_slope", "cmf20"} else "")
        L.append(f"   {a:10} ~ {b:12} {c:+.3f}{note}")
    L.append("")
    L.append("⚠️ 高相关 ⇒ 可以关掉讨论；**低相关 ⇏ 有 alpha**—— "
             "本脚本不测增量预测价值，那要预注册回测。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="量族冗余体检（只算相关不算收益）")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    universe = ([t.upper() for t in a.tickers] if a.tickers
                else sorted(p.stem for p in KLINE_DEEP_DIR.glob("*.jsonl"))[:a.limit])
    r = run(universe)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
