"""sectors.py — ticker → 量比基准映射（单一真源）。

 relative_volume 回测裁决 R 的落地件（设计与冻结表 =
quant/research/relative_volume_gate_design.md 决策点 1，用户 锁定：
半导体标的以 SMH 为基准，其他以 QQQ）。

半导体 = 芯片设计/制造/设备/存储/材料/光电半导体器件——光模块 AAOI/LITE、
衬底 AXTI 归半导体（用户 用法里 SMH 即此 complex 的尺）；WDC 分拆后纯 HDD、
NBIS/CRWV 数据中心运营商、NOK 通信设备归 QQQ。

消费方: quant/decision/regime_transition.volume_leg (live 量腿) +
quant/research/relative_volume_backtest (回测, 同表同源)。
改表 = 改判定, 先过 relative_volume 复测（+12 个月）。
"""
from __future__ import annotations

SEMI_SMH = frozenset({
    "AAOI", "ADI", "AMAT", "AMD", "AMKR", "ASML", "AVGO", "AXTI", "INTC", "KLAC",
    "LITE", "LRCX", "MCHP", "MRVL", "MU", "NVDA", "ON", "QCOM", "QRVO", "SNDK",
    "SWKS", "TER", "TSM", "TXN",
})


def benchmark_for(ticker: str | None) -> str:
    """量比基准: 半导体 → SMH, 其余(含未知) → QQQ。"""
    return "SMH" if str(ticker or "").upper() in SEMI_SMH else "QQQ"
