#!/usr/bin/env python3
"""chain_layers.py — AI 链同层冗余探测器.

⚠️ 这个工具**只**做一件事:把 book 里的持仓按 AI 产业链子驱动分层,
   flag **同一层被押了 ≥2 次**(同一子赌注押多遍 → 该层不独立)。

它**不**做(per memory partner_calibration.md §AI-circle):
   ✗ 不计算总 AI 暴露 %
   ✗ 不建议"分散出 AI / AI 太多"
   ✗ 跨层(不同子驱动)永远不 flag —— 那是 deliberate 链 laddering, 是 additive 敞口

为什么有用(两个合法用途):
   1. 资本效率: 同层两个名字, 第 2 个是 additive conviction 还是只是重复下注?
      (例: CRWV≈NBIS 都是 neocloud 运营层 → 挑一个当 core)
   2. 接 Kelly(kelly-position-sizing.md §7): 同层相关仓位不能各自算 Kelly,
      必须套联合 Kelly, 否则 = 对同一赌注下 N× Kelly = 过注。

LAYER_MAP 在下面, 是**可编辑**的人工映射 —— 用户 自己调taxonomy。
NOK/MRVL 这类跨层名字可给多个 layer。

CLI:
    python3 quant/risk/chain_layers.py [--state <path>] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.occ import parse_occ

from quant.core.paths import POSITIONS_PATH

DEFAULT_STATE = str(POSITIONS_PATH)

# ── 可编辑映射: underlying → 一个或多个链层 ───────────────────────────
# 层级 = AI 链子驱动 (per memory partner_calibration.md §AI-circle):
#   silicon  算力硅 (GPU/CPU/ASIC/foundry)
#   memory   内存 (HBM/DRAM)
#   optical  光互联 (transceiver/CPO/optical DSP)
#   network  网络系统设备 (路由/交换/电信设备, 区别于 optical 器件)
#   neocloud 算力运营 (GPU cloud operator)
#   power    电力 (核电/电网/供电)
LAYER_MAP = {
    "MU":   ["memory"],
    "DRAM": ["memory"],          # ETF, 内存篮子
    "SK":   ["memory"],
    "AAOI": ["optical"],
    "LITE": ["optical"],
    "MRVL": ["silicon", "optical"],   # 跨层: 自研 ASIC + 光 DSP
    "INTC": ["silicon"],
    "TSM":  ["silicon"],
    "NOK":  ["network"],         # 电信/网络设备, 区别于 optical 器件
    "SMR":  ["power"],
    "CRWV": ["neocloud"],
    "NBIS": ["neocloud"],
    "SIVE": ["optical"],
}

LAYER_LABEL = {
    "silicon": "算力硅 (GPU/CPU/ASIC/foundry)",
    "memory": "内存 (HBM/DRAM)",
    "optical": "光互联 (transceiver/CPO/DSP)",
    "network": "网络系统设备 (路由/交换/电信)",
    "neocloud": "算力运营 (GPU cloud)",
    "power": "电力 (核电/电网)",
}


def underlying(code: str) -> str:
    """期权代码 → 标的. NOK270115C15000 → NOK; DRAM261218C70000 → DRAM."""
    parts = parse_occ(str(code).upper())
    return parts["underlying"] if parts else str(code).upper()


def load_active(state_path):
    """返回 {underlying: [held codes]} for qty != 0 的持仓(stock + option)."""
    with open(state_path) as f:
        d = json.load(f)
    book = {}
    for code, v in d.get("tickers", {}).items():
        qty = (v.get("broker") or {}).get("qty") or 0
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0
        if qty == 0:
            continue
        u = underlying(code)
        book.setdefault(u, []).append(code)
    return book


def analyze(book):
    layers = {}   # layer -> set(underlyings)
    unmapped = []
    for u in book:
        lays = LAYER_MAP.get(u)
        if not lays:
            unmapped.append(u)
            continue
        for lay in lays:
            layers.setdefault(lay, set()).add(u)
    redundant = {lay: sorted(names) for lay, names in layers.items() if len(names) >= 2}
    return layers, redundant, unmapped


def fmt(book, layers, redundant, unmapped):
    L = []
    L.append("=" * 64)
    L.append("AI 链同层冗余探测  (只查同层重复下注; 不算总 AI%, 不建议减 AI)")
    L.append("=" * 64)
    L.append(f"  active 标的: {', '.join(sorted(book))}")
    L.append("-" * 64)
    L.append("  按子驱动层分布:")
    for lay in sorted(layers, key=lambda x: -len(layers[x])):
        names = sorted(layers[lay])
        tag = "  ⚠ 同层 ≥2" if len(names) >= 2 else ""
        L.append(f"    {lay:9} {LAYER_LABEL.get(lay,''):28} {', '.join(names)}{tag}")
    if unmapped:
        L.append(f"    [未映射] {', '.join(sorted(unmapped))}  → 在 LAYER_MAP 补")
    L.append("-" * 64)
    if redundant:
        L.append("  🔎 同层冗余候选(每个问自己: 第 2 个是 additive 还是重复?):")
        for lay, names in redundant.items():
            L.append(f"    • {LAYER_LABEL.get(lay, lay)}: {' + '.join(names)}")
            L.append(f"      → 同一子赌注押了 {len(names)} 遍。决策: (a) 挑一个当 core, "
                     f"或 (b) 确认各自 additive;")
            L.append(f"        (c) Kelly 上这一层套**联合 Kelly**, 不是各自 Kelly"
                     f"(kelly-position-sizing.md §7)")
    else:
        L.append("  ✓ 无同层冗余 —— 每个子驱动层最多一个名字, 全是错位补充。")
    L.append("=" * 64)
    L.append("  注: 跨层(不同子驱动)= deliberate 链 laddering = additive, 永不 flag。")
    L.append("      全仓 AI 是 thesis 不是 bug (memory partner_calibration.md §AI-circle)。")
    L.append("=" * 64)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="AI 链同层冗余探测器")
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not Path(a.state).exists():
        sys.exit(f"[chain_layers] state 不存在: {a.state}")
    book = load_active(a.state)
    layers, redundant, unmapped = analyze(book)
    if a.json:
        print(json.dumps({
            "active": sorted(book),
            "layers": {k: sorted(v) for k, v in layers.items()},
            "redundant_layers": redundant,
            "unmapped": sorted(unmapped),
        }, ensure_ascii=False, indent=2))
    else:
        print(fmt(book, layers, redundant, unmapped))


if __name__ == "__main__":
    main()
