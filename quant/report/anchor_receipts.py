#!/usr/bin/env python3
"""anchor_basis 收据校验 —— 把卡片里引的数字拿去和当下的 level_map 对账。

为什么存在 (, 用户: 「随便一指就发现了你的 gate 错误, 那让我怎么
相信其他是对的呢」): `gate_audit` 查的六条全是**内部自洽**, 一张锚错的卡照样
全绿。锚对不对属于「①锚是不是 tape 地标」, 此前完全靠人眼。

本模块堵两个具体漏洞 —— **不是判断力, 是收据**:
  A. **引了不存在的簇**卡上写 `pivot 592.05-601.00 x6`, 现在算不出这个簇
  B. **证据已经衰减**卡上写 `拒x17`, 现在只有 12 —— 窗口滚过去了, 当初
                          支撑这条线的证据没了 (卡片自己写过「证据只增不减」,
                          所以**减了**就是信号)

不堵的: 「引的簇是真的, 但选错了那一个」—— 那是判断, 归 `gate_audit.advise_card`
的①族与人眼。⚠️ 本模块**永远不判 pass/fail 交易动作**, 只产出待复核清单。

判定窗口 = level_map 自己的 `near_window` (一个 profile 桶宽 ≈0.25N), 与信号
2/2b/4 同分辨率 —— 不另拍一个容差。

CLI: .venv/moomoo/bin/python3 quant/report/anchor_receipts.py [--ticker T] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import json
import re
from typing import Any, Dict, List, Optional

from quant.core.paths import POSITIONS_PATH, WATCHLIST_PATH
from quant.report.gate_audit import auditable_cards

# `<lo>-<hi> ... x<N>` 且**必须自证是 pivot 簇**—— 前有 "pivot"/"簇" 或后带
# "[high"/"[low"。不加这道门就会把 VP 带 ("740-760 8%")、zone 区间 ("95.5-98.7")
# 全当成簇引用报出来 (首版实测: 30 条 findings 里七成是这种)。
CLUSTER_RE = re.compile(
    r"(?:pivot|簇)[^,;。\n]{0,12}?(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)"
    r"[^,;。\n]{0,16}?[x×]\s*(\d+)"
    r"|(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:pivot)?\s*[x×]\s*(\d+)\s*\[(?:high|low)")
# `拒x17` / `拒 x 17` / `拒绝 17` / `level 392 拒19`
REJECT_RE = re.compile(r"拒(?:绝)?\s*[x×]?\s*(\d+)")
# 拒绝计数前面最近的那个**价位**数字。带单位后缀的一律不是价位 ——
# 「90 根日线内拒 ×10」里的 90 是 bar 数, 首版把它读成 level 90 报了假衰减
# (AAOI  实测; spot 133.93 让 [0.5x,2x] 区间过滤没拦住)。
UNIT_SUFFIX = r"(?!\s*(?:根|日|天|周|月|年|个|次|档|层|轮|倍|分|秒|%|％|m\b|N\b))"
LEVEL_BEFORE_RE = re.compile(
    r"(\d+(?:\.\d+)?)" + UNIT_SUFFIX + r"(?=[^\d]{0,26}拒(?:绝)?\s*[x×]?\s*\d+)")


def basis_texts(agent: dict) -> List[tuple]:
    """卡上所有写着证据的地方 → [(where, text)]。"""
    out = []
    z = agent.get("zones") or {}
    b = ((z.get("anchored_on") or {}).get("basis"))
    if b:
        out.append(("zones.anchored_on.basis", b))
    nr = agent.get("next_regime") or {}
    gb = (((nr.get("entry_gate") or {}).get("anchored_on") or {}).get("basis"))
    if gb:
        out.append(("next_regime.entry_gate.anchored_on.basis", gb))
    lr = agent.get("last_regime") or {}
    rb = (((lr.get("repair_gate") or {}).get("anchored_on") or {}).get("basis"))
    if rb:
        out.append(("last_regime.repair_gate.anchored_on.basis", rb))
    return out


def cited_clusters(text: str) -> List[Dict[str, Any]]:
    """文本里引到的 pivot 簇 —— [(lo, hi, touches)]。lo>hi 的匹配丢弃 (多半是日期/区间倒写)。"""
    out = []
    for m in CLUSTER_RE.finditer(text or ""):
        g = [x for x in m.groups() if x]
        if len(g) != 3:
            continue
        lo, hi, n = float(g[0]), float(g[1]), int(g[2])
        if lo < hi and n >= 2:
            out.append({"lo": lo, "hi": hi, "touches": n})
    return out


def cited_rejections(text: str, spot: Optional[float] = None) -> List[Dict[str, Any]]:
    """文本里引到的拒绝计数 —— [(level, count)]。

    ⚠️ **必须给 spot 才有意义**: 「拒」前面最近的数字经常不是价位 (ATR14、
    百分比、日期都会命中)。 首版无此过滤, 报出 `level 14 拒x8`、
    `level 1 拒x5` 这类垃圾 —— 假阳性会训练人无视告警, 比没有这条检查更坏。
    价位可信区间取 [0.5x, 2x] spot: 卡上引的线都在自己 regime 附近, 不会离谱。
    """
    out = []
    for m in REJECT_RE.finditer(text or ""):
        seg = (text or "")[:m.end()]
        lv = LEVEL_BEFORE_RE.findall(seg)
        if not lv:
            continue
        level = float(lv[-1])
        if spot and not (0.5 * spot <= level <= 2.0 * spot):
            continue                       # 不是这只票的价位, 丢弃
        out.append({"level": level, "count": int(m.group(1))})
    return out


def check_clusters(cited: List[dict], live_clusters: List[dict],
                   tol: float) -> List[str]:
    """引的簇现在还算得出来吗。tol = level_map 的判定窗口 (一个桶宽)。"""
    out = []
    for c in cited:
        # 簇**长大/合并**不是失败: live 簇包住引用簇且触碰数不少 = 证据增强。
        #  实测 COHR 引 304.06-309.19 ×3, 现在是 291-309.19 ×5 ——
        # 边界变了但那堆 pivot 一个没少, 报成「算不出」是误报。
        if any(k["lo"] <= c["lo"] + tol and k["hi"] >= c["hi"] - tol
               and k["touches"] >= c["touches"] for k in live_clusters):
            continue
        hits = [k for k in live_clusters
                if abs(k["lo"] - c["lo"]) <= tol and abs(k["hi"] - c["hi"]) <= tol]
        if not hits:
            near = min(live_clusters,
                       key=lambda k: abs(k["center"] - (c["lo"] + c["hi"]) / 2),
                       default=None)
            out.append(f"A 引用簇 {c['lo']:g}-{c['hi']:g} ×{c['touches']} 现在算不出"
                       + (f" (最近的是 {near['lo']:g}-{near['hi']:g} ×{near['touches']}"
                          f" [{near['kinds']}] last {near['last_touch']})" if near else ""))
            continue
        best = max(hits, key=lambda k: k["touches"])
        if best["touches"] < c["touches"]:
            out.append(f"B 引用簇 {c['lo']:g}-{c['hi']:g} 触碰数已衰减: "
                       f"引 ×{c['touches']} → 现 ×{best['touches']} "
                       f"(last {best['last_touch']})")
    return out


def check_rejections(cited: List[dict], interactions_of, tol: float) -> List[str]:
    """引的拒绝计数现在还够吗。**只增不减**—— 减了就是当初的证据没了。"""
    out = []
    for c in cited:
        iv = interactions_of(c["level"])
        if iv is None:
            continue
        now = iv.get("n_rejections")
        if now is None:
            continue
        if now < c["count"]:
            last = (iv.get("recent_rejections") or [{}])[-1].get("date", "?")
            out.append(f"B level {c['level']:g} 拒绝计数已衰减: 引 ×{c['count']} → "
                       f"现 ×{now} (最近一次 {last}) —— 卡片约定「证据只增不减」")
    return out


def audit_ticker(ticker: str, agent: dict, *, map_of, spot_of=None) -> List[str]:
    """一张卡的收据问题清单 (空 = 引用的数字都对得上)。

    `map_of(ticker, levels) -> dict | None` 注入 level_map.build_map 的产物;
    None = 拉不到 bars, 本卡跳过 (缺数据 ≠ 不合规)。
    """
    spot_of = spot_of or (lambda t: None)
    findings: List[str] = []
    all_cited_levels = []
    per_text = []
    spot = spot_of(ticker)
    for where, text in basis_texts(agent):
        cl, rj = cited_clusters(text), cited_rejections(text, spot)
        per_text.append((where, cl, rj))
        all_cited_levels += [r["level"] for r in rj]
    if not per_text:
        return []
    m = map_of(ticker, sorted(set(all_cited_levels)))
    if not m:
        return []
    tol = m.get("near_window") or 0.0
    live = (m.get("pivot_clusters_below") or []) + (m.get("pivot_clusters_above") or [])
    by_level = {round(iv["level"], 4): iv for iv in (m.get("levels") or [])}

    def interactions_of(lv):
        return by_level.get(round(float(lv), 4))

    for where, cl, rj in per_text:
        for msg in check_clusters(cl, live, tol) + check_rejections(rj, interactions_of, tol):
            findings.append(f"[{where}] {msg}")
    return findings


def _default_map_of(ticker: str, levels: List[float]):
    from quant.decision import level_map as lm     # noqa: PLC0415  CLI-only, 拉 OpenD
    bars = lm.cached_bars(ticker, 260)
    if not bars or len(bars) < 60:
        return None
    pb, src = lm.fetch_profile_bars(ticker, 60)
    return lm.build_map(bars, levels=levels, profile_bars=pb, profile_source=src)


def _spot_of(ticker: str) -> Optional[float]:
    from quant.core.io_json import iter_jsonl                     # noqa: PLC0415
    from quant.core.paths import KLINE_CACHE_DIR                  # noqa: PLC0415
    rows = list(iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl"))
    return rows[-1].get("close") if rows else None


def run(*docs: Optional[dict], only: Optional[str] = None,
        map_of=None) -> List[Dict[str, Any]]:
    map_of = map_of or _default_map_of
    out = []
    for t, agent in auditable_cards(*docs):
        if only and t != only:
            continue
        try:
            issues = audit_ticker(t, agent, map_of=map_of, spot_of=_spot_of)
        except Exception as e:                                    # noqa: BLE001
            issues = [f"⚠️ 校验失败 {type(e).__name__}: {e}"]
        if issues:
            out.append({"ticker": t, "issues": issues})
    return out


def main() -> int:
    only = None
    if "--ticker" in sys.argv:
        only = sys.argv[sys.argv.index("--ticker") + 1].upper()
    docs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (POSITIONS_PATH, WATCHLIST_PATH) if p.exists()]
    findings = run(*docs, only=only)
    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
        return 0
    if not findings:
        print("✅ anchor_basis 引用的簇与拒绝计数, 全部与当下 level_map 对得上")
        return 0
    print("🧾 收据对不上 (A=引用物不存在 / B=证据已衰减; 待复核, 不是判决):\n")
    for f in findings:
        print(f"🔎 {f['ticker']}")
        for i in f["issues"]:
            print(f"     {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
