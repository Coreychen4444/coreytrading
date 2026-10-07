#!/usr/bin/env python3
"""macro_lens_log.py — 宏观透镜每日发布 + 账本 + 推送（判读层，零授权效力）。

用途：用指标与数据判断当前市场环境、哪个因素在驱动市场、市场在交易什么与未来可能交易的方向，
并只保留对交易有信息量的部分。

判别式与研究收据的真源 `quant/core/macro_lens.py`。

  --update   收盘后：刷新宏观日线（data/macro_series，尾部覆盖）→ 读数 → 组合暴露 / 日历 / 情报触发源 →
             写 `trade/intelligence/macro/current.json`（覆盖）+ `ledger.jsonl`（按 asof 幂等追加）+ 当日全文。
             `--session D`（定时任务传 due 返回的交易日）：读数 asof ≠ D = 今天的日线没取到 → 不写任何文件、退出码 3
             （不拿上一交易日的读数冒充今天）。日线没到 asof 的品种写进 `data_gaps`，推送首段 ⚠️ 播出。
  --digest   推送段（从 current.json 渲染；macro-lens 定时任务单独推一条）。
  --brief    盘前简报用的四行（current.json + 隔夜期货变动）。
  --report   全文 Markdown。
  --week     周回顾（读账本：档位 / 最强驱动 / 剧本轨迹、读法翻转、上周最强驱动的对账）。
  --explain T [--date D]  某日 T 的涨跌 = 宏观解释（前一日读法链因子 × 40 日多元 β，无前视）+ 残差 → 宏观驱动 / 混合 / 非宏观。
  --json     结构化读数。

账本的问责：每天一行 = 当天的档位 / 最强驱动 / 读法 / 接棒 / 情景；h 日之后的实际就是 h 日之后那一行，对账 = 两行 join。
🚨 零授权：不改 PLAN / VETO / cap / 股数，不写卡，不写 decisions，零模型调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402
from typing import Dict, List, Optional, Sequence  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from quant.core import macro_lens as L  # noqa: E402
from quant.core.holdings import is_managed  # noqa: E402
from quant.core.io_json import atomic_write_json, iter_jsonl, read_json, state_lock  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import (INTELLIGENCE_CALENDAR_PATH, INTELLIGENCE_REPORTS_DIR, KLINE_CACHE_DIR,  # noqa: E402
                              MACRO_CURRENT_PATH, MACRO_EVENT_CALENDAR_PATH, MACRO_LEDGER_PATH,
                              MACRO_LENS_LOCK_PATH, MACRO_REPORTS_DIR, NAV_HISTORY_PATH, POSITIONS_PATH)
from quant.data import macro_series  # noqa: E402

ET = ZoneInfo("America/New_York")
CATALYST_DAYS = 30
ARCHIVE_DAYS = 3
BIG3 = {"FOMC": "FOMC 利率决议", "CPI": "CPI", "NFP": "非农就业报告"}


# ════════════════════════════════════════════════════════════
# 装配：序列 / 组合 / 日历 / 情报
# ════════════════════════════════════════════════════════════

def load_series(root: Optional[Path] = None) -> Dict[str, List]:
    return {k: macro_series.load(k, root) for k in macro_series.SERIES}


def refresh() -> List[str]:
    """尾部覆盖式更新全部宏观序列；返回失败的 KEY（单品种失败不拖全局）。"""
    failed = []
    for k in macro_series.SERIES:
        try:
            macro_series.update(k)
        except Exception as e:                                              # noqa: BLE001
            print(f"[macro_lens] {k} 刷新失败：{type(e).__name__}: {e}", file=sys.stderr)
            failed.append(k)
    return failed


YAHOO_KEYS = frozenset(k for k, (src, _code, _value) in macro_series.SERIES.items() if src == "yahoo")


def data_gaps(series: Dict[str, List], asof: str, failed: Sequence[str] = ()) -> Dict:
    """数据收据（监控不许无声变黑）：日线没到 asof 的 Yahoo 品种 + 刷新失败的 KEY。
    FRED 次日才发布、天然滞后，不算 lagging（读数自带 fred_last）。缺的那天该因子变动 = None（相关窗口少一个样本），
    水平量沿用 ≤3 日 —— 读数照出，但必须说出来。"""
    lagging = [{"key": k, "last": rows[-1][0] if rows else None}
               for k, rows in sorted(series.items())
               if k in YAHOO_KEYS and (not rows or rows[-1][0] < asof)]
    return {"lagging": lagging, "refresh_failed": sorted(failed)}


def _kline_closes(ticker: str, kdir: Path = KLINE_CACHE_DIR) -> Dict[str, float]:
    out = {}
    for r in iter_jsonl(kdir / f"{ticker}.jsonl"):
        c = coerce_float(r.get("close"))
        d = str(r.get("time") or r.get("date") or "")[:10]
        if d and c and c > 0:
            out[d] = c
    return out


def _latest_nav(path: Path = NAV_HISTORY_PATH) -> Optional[float]:
    last = None
    for r in iter_jsonl(path):
        last = r
    return coerce_float((last or {}).get("total_assets"))


def book_weights(doc: dict, nav: Optional[float], kdir: Path = KLINE_CACHE_DIR) -> Dict[str, float]:
    """受管持仓按标的汇总的方向名义 ÷ NAV（正股 = 股数 × 价；期权 = 张数 × 乘数 × delta × 标的价）。"""
    if not nav:
        return {}
    out: Dict[str, float] = {}
    for tk, body in ((doc or {}).get("tickers") or {}).items():
        if not isinstance(body, dict) or not is_managed(body):
            continue
        br = body.get("broker") or {}
        qty = coerce_float(br.get("qty")) or 0.0
        if not qty:
            continue
        occ = parse_occ(tk)
        und = occ["underlying"] if occ else tk
        closes = _kline_closes(und, kdir)
        px = closes[max(closes)] if closes else None
        if not px:
            continue
        if occ:
            delta = coerce_float(br.get("delta"))
            delta = 1.0 if delta is None else delta
            if occ.get("side") == "P" and delta > 0:
                delta = -delta
            notional = qty * (occ.get("multiplier") or 100) * delta * px
        else:
            notional = qty * px
        out[und] = out.get(und, 0.0) + notional / nav
    return out


def book_block(state_ctx, doc: Optional[dict] = None, nav: Optional[float] = None,
               kdir: Path = KLINE_CACHE_DIR) -> Optional[Dict]:
    """组合对最强驱动 / 接棒因子的 40 日 β 暴露 + 情景下的组合变动（占 NAV）。"""
    fr, ch, t, factors, shocks = state_ctx
    doc = doc if doc is not None else (read_json(POSITIONS_PATH) if POSITIONS_PATH.exists() else {})
    nav = nav if nav is not None else _latest_nav()
    w = book_weights(doc, nav, kdir)
    if not w:
        return None
    dates = fr["dates"]
    rets = {}
    for tk in w:
        closes = _kline_closes(tk, kdir)
        vals = [closes.get(d) for d in dates]
        rets[tk] = L._ln_diff(vals)
    out = {"nav": nav, "weights": w, "gross": sum(abs(v) for v in w.values()), "factors": {}}
    for f in factors:
        ex = L.book_exposure(rets, w, ch[f], t)
        if ex is None:
            continue
        sd = shocks.get(f)
        ex["move"] = ex["beta"] * sd if sd is not None else None
        out["factors"][f] = ex
    return out


def catalysts(asof: str, days: int = CATALYST_DAYS, *, cal_path: Path = INTELLIGENCE_CALENDAR_PATH,
              big3_path: Path = MACRO_EVENT_CALENDAR_PATH) -> List[Dict]:
    """asof 之后 days 天内的发布：情报日历（已核官方，7 日滚动）∪ FOMC / CPI / 非农官方日历。"""
    end = (date.fromisoformat(asof) + timedelta(days=days)).isoformat()
    rows: List[Dict] = []
    seen = set()
    if cal_path.exists():
        for e in (read_json(cal_path).get("events") or []):
            d = str(e.get("scheduled_at_et") or "")[:10]
            if asof < d <= end:
                ch = L.event_channel(str(e.get("name") or ""))
                rows.append({"date": d, "time": str(e.get("scheduled_at_et") or "")[11:16], "name": e.get("name"),
                             "factors": list(ch[0]) if ch else [], "channel": ch[1] if ch else None,
                             "tier": L.event_tier(str(e.get("name") or "")), "source": "情报日历"})
                seen.add((d, ch[1] if ch else None))
    if big3_path.exists():
        for kind, evs in (read_json(big3_path).get("events") or {}).items():
            for e in evs:
                d = str(e.get("date") or "")
                if not (asof < d <= end) or e.get("scheduled") is False:
                    continue
                name = BIG3.get(kind, kind)
                ch = L.event_channel(name if kind != "NFP" else "非农")
                key = (d, ch[1] if ch else None)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"date": d, "time": "14:00" if kind == "FOMC" else "08:30", "name": name,
                             "factors": list(ch[0]) if ch else [], "channel": ch[1] if ch else None,
                             "tier": "A", "source": "官方日历"})
    rows.sort(key=lambda r: (r["date"], r["time"] or ""))
    return rows


_HEADLINE = re.compile(r"^\*\*\d+[．.]\s*(.+?)\*\*")


def archive_hits(factor: str, asof: str, days: int = ARCHIVE_DAYS, *,
                 reports_dir: Path = INTELLIGENCE_REPORTS_DIR, limit: int = 2) -> List[Dict]:
    """情报归档（最近 days 天的新闻报告，若配置）里命中因子关键词的标题行。命中不了 = 空（= 触发源未知，合法输出）。"""
    keys = L.ARCHIVE_KEYWORDS.get(factor) or ()
    start = (date.fromisoformat(asof) - timedelta(days=days)).isoformat()
    files = []
    for p in reports_dir.glob("*/*.md"):
        d = p.name[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}$", d) and start <= d <= (date.fromisoformat(asof) + timedelta(days=1)).isoformat():
            files.append((d, p))
    out, seen = [], set()
    for d, p in sorted(files, reverse=True):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _HEADLINE.match(line.strip())
            if not m:
                continue
            head = m.group(1).strip()
            if head in seen or not any(k.lower() in head.lower() for k in keys):
                continue
            seen.add(head)
            out.append({"date": d, "file": str(p.relative_to(reports_dir.parents[1])), "headline": head})
            if len(out) >= limit:
                return out
    return out


# ════════════════════════════════════════════════════════════
# 构建与发布
# ════════════════════════════════════════════════════════════

def build(asof: Optional[str] = None, *, series=None, doc=None, nav=None, kdir: Path = KLINE_CACHE_DIR,
          cal_path: Path = INTELLIGENCE_CALENDAR_PATH, big3_path: Path = MACRO_EVENT_CALENDAR_PATH,
          reports_dir: Path = INTELLIGENCE_REPORTS_DIR, refresh_failed: Sequence[str] = ()) -> Dict:
    series = series if series is not None else load_series()
    state = L.assess(series, asof)
    state["data_gaps"] = data_gaps(series, state["asof"], refresh_failed)
    fr, ch, t = L.context(series, state["asof"])
    drv = state["drivers"]
    factors = [f for f in [drv["dominant"], *drv.get("co_dominant", []), drv["challenger"]] if f]
    for f in factors:
        if f in L.DOMINANT_HISTORY and f not in state["dominant_history"]:
            state["dominant_history"][f] = dict(zip(("n", "fut_r2", "uncond", "sign_keep"), L.DOMINANT_HISTORY[f]))
    factors = list(dict.fromkeys(factors))
    shocks = {s["factor"]: s["shock"] for s in state["forward"]["scenarios"]}
    for f in factors:
        if f not in shocks:
            sc = L.scenarios(ch, f, t)
            if sc:
                state["forward"]["scenarios"].append(sc)
                shocks[f] = sc["shock"]
    state["book"] = book_block((fr, ch, t, factors, shocks), doc, nav, kdir)
    state["catalysts"] = catalysts(state["asof"], cal_path=cal_path, big3_path=big3_path)
    hit = set(factors)
    for c in state["catalysts"]:
        c["hits_driver"] = bool(hit & set(c["factors"])) and c["tier"] in ("A", "B")
    state["triggers"] = {f: archive_hits(f, state["asof"], reports_dir=reports_dir) for f in factors}
    state["generated_at"] = datetime.now(ET).isoformat(timespec="seconds")
    state["authority"] = "none"
    return _json_safe(state)


def _json_safe(o):
    if isinstance(o, float):
        return None if not math.isfinite(o) else o
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _breadth_row(br: Optional[Dict]) -> Optional[Dict]:
    if not br:
        return None
    return {"rsp": br["equal_weight"], "iwm": br["small_cap"],
            "sectors_above_50dma": [br["sectors_above_50dma"]["n"], br["sectors_above_50dma"]["of"]],
            "B60": br["B60"]}


def ledger_row(s: Dict) -> Dict:
    drv = s["drivers"]
    return {
        "asof": s["asof"], "captured_at": s["generated_at"], "schema": s["schema"],
        "tier": {k: s["risk"]["tier"][k] for k in ("count", "known", "tier", "on")},
        "states": {c: {"on": v["on"], "value": v.get("value"), "pct": v.get("pct")}
                   for c, v in s["risk"]["states"].items()},
        "drivers": [{k: r.get(k) for k in ("factor", "rho", "r2", "pct", "rho20", "r2_lag", "trend")}
                    for r in drv["rows"]],
        "M": drv["M"], "pct_M": drv["pct_M"], "dominant": drv["dominant"],
        "co_dominant": drv.get("co_dominant") or [], "challenger": drv["challenger"],
        "theme": s["story"]["theme"], "rates_split": s.get("rates_split"),
        "leaders": [[r["ticker"], r["rel"]] for r in s["story"]["leaders"]],
        "breadth": _breadth_row(s.get("breadth")),
        "book": {f: {"beta": v["beta"], "move": v.get("move")}
                 for f, v in ((s.get("book") or {}).get("factors") or {}).items()},
        "data_gaps": {"lagging": [x["key"] for x in (s.get("data_gaps") or {}).get("lagging") or []],
                      "refresh_failed": (s.get("data_gaps") or {}).get("refresh_failed") or []},
        "design": L.DESIGN_DOC,
    }


def publish(s: Dict, *, current: Path = MACRO_CURRENT_PATH, ledger: Path = MACRO_LEDGER_PATH,
            reports: Path = MACRO_REPORTS_DIR, lock_path: Path = MACRO_LENS_LOCK_PATH) -> Dict:
    # 账本「查重 + 追加」必须原子：同一 asof 可能有两个发布者（定时任务与 session 里手跑的 --update）
    with state_lock(lock_path):
        atomic_write_json(current, s)
        have = {r.get("asof") for r in iter_jsonl(ledger)} if ledger.exists() else set()
        appended = False
        if s["asof"] not in have:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ledger_row(s), ensure_ascii=False) + "\n")
            appended = True
        rp = reports / s["asof"][:7] / f"{s['asof']}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report(s) + "\n", encoding="utf-8")
    return {"current": str(current), "ledger_appended": appended, "report": str(rp)}


# ════════════════════════════════════════════════════════════
# 渲染
# ════════════════════════════════════════════════════════════

def _pct(x: Optional[float], nd: int = 1, sign: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def _ap(p: Optional[float]) -> str:
    return "—" if p is None else f"{p * 100:.0f}"


def _shock_txt(f: str, sd: float) -> str:
    return "±" + L.display_move(f, sd).lstrip("+")


def _move_txt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{'∓' if x < 0 else '±'}{abs(x) * 100:.1f}%"


def _row(drv: Dict, f: str) -> Optional[Dict]:
    return next((r for r in drv["rows"] if r["factor"] == f), None)


def _driver_line(r: Dict) -> str:
    return (f"{r['cn']}（{L.FACTOR_UP[r['factor']]}→股{'跌' if (r['rho'] or 0) < 0 else '涨'}，ρ {r['rho']:+.2f}，"
            f"解释 SPY 40 日波动 {r['r2'] * 100:.0f}%，3年{_ap(r['pct'])}分位）")


def _extreme_txt(key: str, rd: Dict) -> Optional[str]:
    ex = (rd or {}).get("extreme")
    if not ex:
        return None
    since = ex["since"][:7]
    unit = "%" if key in ("US10Y", "US3M") else ""
    return f"{rd['cn']} {rd['value']:.2f}{unit} = {'有数据以来' if ex.get('record') else since + ' 以来'}最{ex['kind']}"


def gap_line(s: Dict) -> Optional[str]:
    """⚠️ 数据行：日线没到 asof 的品种（括号 = 最新日）+ 其余刷新失败的 KEY；无缺口 = None。"""
    g = s.get("data_gaps") or {}
    lag = g.get("lagging") or []
    parts = []
    if lag:
        parts.append(f"日线未到 {s['asof'][5:]}：" + "、".join(
            f"{x['key']}（{x['last'][5:] if x.get('last') else '无数据'}）" for x in lag))
    other = [k for k in g.get("refresh_failed") or [] if k not in {x["key"] for x in lag}]
    if other:
        parts.append("刷新失败 " + "、".join(other) + "（用缓存）")
    return ("⚠️ 数据：" + "；".join(parts) + " —— 缺的那天该因子不计、水平量沿用 ≤3 日") if parts else None


def breadth_line(br: Optional[Dict]) -> Optional[str]:
    """广度一行（描述性，零授权）：等权 / 小盘相对 SPY + 行业站上 50 日线 + B60「高位 + 窄」。"""
    if not br:
        return None
    eq, sm, sec = br["equal_weight"], br["small_cap"], br["sectors_above_50dma"]

    def rel(x, lag):
        d, p = x.get(f"d{lag}"), x.get(f"pct{lag}")
        return "—" if d is None else f"{_pct(d)}" + ("" if p is None else f"（{_ap(p)}分位）")
    parts = [f"等权 RSP 相对 SPY 20 日 {rel(eq, 20)} · 60 日 {rel(eq, 60)}", f"小盘 IWM 20 日 {rel(sm, 20)}"]
    if sec["of"]:
        parts.append(f"行业站上 50 日线 {sec['n']}/{sec['of']}" + (f"（{'、'.join(sec['names'])}）" if sec["names"] else ""))
    flag = "；⚑ 高位 + 窄（B60 成立）" if br["B60"]["on"] else ""
    return "- 广度（3年分位）：" + "｜".join(parts) + flag + " —— 形状描述，已检验不预告方向"


def digest(s: Dict) -> str:
    drv, tier, st = s["drivers"], s["risk"]["tier"], s["risk"]["states"]
    b = tier["base"]
    on = "、".join(tier["on"]) or "无"
    gap = gap_line(s)
    lines = [f"## 🌐 宏观透镜 {s['asof'][5:]} 收盘 · 判读层，零授权",
             *([gap] if gap else []),
             f"**环境：{tier['tier']} — 六盏风险灯亮 {tier['count']}（{on}）**",
             f"> 历史同档，之后 20 日：平均最大回撤 {b['dd']:.1f}%｜≥5% 回撤 {b['p_dd5']:.0f}%｜年化波动 {b['rv']:.0f}%｜"
             f"平均收益 {b['ret']:+.1f}%。亮灯预告的是波动和回撤深度，不是方向。", "",
             "| 灯 | 读数 | |", "|---|---|---|"]
    for c in L.RISK_LIGHTS:
        x = st[c]
        mark = "🔴" if x["on"] else ("⚪" if x["on"] is False else "❔")
        lines.append(f"| {x['name']} | {x['text']} | {mark} |")
    lines.append("")
    dom = _row(drv, drv["dominant"]) if drv["dominant"] else None
    if dom:
        age = (s.get("sign_age") or {}).get(dom["factor"])
        lines.append(f"**近 40 日最强驱动：{_driver_line(dom)}**" + (f" · 读法已持续 {age} 日" if age else ""))
    else:
        top = drv["rows"][0] if drv["rows"] else None
        lines.append("**近 40 日最强驱动：无（没有异常突出的宏观因子）**"
                     + (f"（最强：{_driver_line(top)}）" if top and top.get("rho") is not None else ""))
    for f in drv.get("co_dominant") or []:
        r = _row(drv, f)
        lines.append(f"- 并列：{_driver_line(r)}" + (f" 🔺10 日前 {r['r2_lag'] * 100:.0f}%" if r.get("trend") == "升温" else ""))
    lines.append(f"- 宏观主导度（8 因子合计解释 SPY 波动）{_pct(drv['M'], 0, False)}，3年{_ap(drv['pct_M'])}分位"
                 + ("；⚑ 历史上此档之后 20 日大盘偏强（样本外 +1.8pp，来自大盘漂移不是选股，零授权）" if s["forward"].get("m_high") else ""))
    lines.append("")
    sto = s["story"]
    lines.append(f"**市场在交易：{sto['theme']}** — {sto['why']}")
    ch = [c["text"].split("（")[0] for c in sto["chain"][:5]]
    if ch:
        lines.append("- 读法：" + " · ".join(ch))
    rs = s.get("rates_split")
    ex10 = _extreme_txt("US10Y", s["readings"].get("US10Y"))
    if rs:
        driver = "实际利率" if abs(rs["real_bp"]) >= 2 * abs(rs["breakeven_bp"]) else (
            "通胀预期" if abs(rs["breakeven_bp"]) >= 2 * abs(rs["real_bp"]) else "两者")
        lines.append(f"- {ex10 or ('10Y ' + format(rs['level'], '.2f') + '%')}；近 20 日 {rs['nominal_bp']:+.0f}bp = "
                     f"实际利率 {rs['real_bp']:+.0f}bp + 通胀预期 {rs['breakeven_bp']:+.0f}bp → 涨的主要是{driver}"
                     f"（FRED 截至 {rs['asof'][5:]}）")
    lead = sto["leaders"]
    if len(lead) >= 6:
        buy = "、".join(f"{r['cn']} {r['ticker']} {_pct(r['rel'])}" for r in lead[:2])
        sell = "、".join(f"{r['cn']} {r['ticker']} {_pct(r['rel'])}" for r in lead[-3:][::-1])
        lines.append(f"- 资金（20 日相对 SPY）：买 {buy}；卖 {sell}")
    bl = breadth_line(s.get("breadth"))
    if bl:
        lines.append(bl)
    trig = []
    for f, hits in (s.get("triggers") or {}).items():
        for h in hits[:1]:
            trig.append(f"{L.FACTOR_CN[f]}｜{h['headline'][:60]}（{h['date'][5:]}）")
    lines.append("- 相关情报（归档关键词命中，不等于因果）：" + ("；".join(trig) if trig else "无命中 → 触发源未知"))
    lines.append("")
    lines.append("**未来方向**")
    chal = _row(drv, drv["challenger"]) if drv["challenger"] else None
    if chal:
        lines.append(f"- 接棒：{chal['cn']}（10 日内解释力 {chal['r2_lag'] * 100:.0f}% → {chal['r2'] * 100:.0f}%）"
                     + ("，已与最强驱动并列" if chal["factor"] in (drv.get("co_dominant") or []) else ""))
    others = [r for r in drv["rows"] if r.get("trend") == "升温" and r["factor"] not in (drv["challenger"], drv["dominant"])
              and (r["r2"] or 0) >= L.R2_DOM]
    for r in others[:2]:
        lines.append(f"- 同步升温：{r['cn']}（{r['r2_lag'] * 100:.0f}% → {r['r2'] * 100:.0f}%）")
    scs = s["forward"]["scenarios"]
    book = (s.get("book") or {}).get("factors") or {}
    if scs:
        hdr = [x["ticker"] for x in scs[0]["rows"]][:5]
        lines += ["", "| 情景（1σ = 20 日典型变动；近 40 日 β） | " + " | ".join(hdr) + " | 你的书* |",
                  "|---|" + "---|" * (len(hdr) + 1)]
        for sc in scs:
            mv = {x["ticker"]: x["move"] for x in sc["rows"]}
            bk = book.get(sc["factor"], {}).get("move")
            lines.append(f"| {L.FACTOR_CN[sc['factor']]} {_shock_txt(sc['factor'], sc['shock'])} | "
                         + " | ".join(_move_txt(mv.get(k)) for k in hdr) + f" | {_move_txt(bk)} |")
        bk = s.get("book") or {}
        if bk.get("weights"):
            lines.append("")
            lines.append(f"> *你的书 = 占 NAV，按方向名义（期权 × delta）{bk['gross'] * 100:.0f}% NAV 的一阶近似；"
                         "期权最坏亏损 = 保费，大幅波动时非线性。")
        lines.append("")
    for w in s["forward"]["flip_watch"][:2]:
        lines.append(f"- 换剧本信号：{w}")
    cats = [c for c in s.get("catalysts") or [] if c.get("channel") and c.get("tier") in ("A", "B")]
    if cats:
        rank = lambda c: (not c.get("hits_driver"), c["tier"], c["date"])      # noqa: E731
        pick = sorted(cats, key=rank)[:5]
        pick.sort(key=lambda c: c["date"])
        lines.append("- 日历（★ = 直接击中当前最强驱动 / 接棒链条）：" + " · ".join(
            f"{'★' if c.get('hits_driver') else ''}{c['date'][5:]} {c['name']}" for c in pick))
    hist = [(f, h) for f, h in (s.get("dominant_history") or {}).items()
            if f in [drv["dominant"], *(drv.get("co_dominant") or [])]]
    if hist:
        lines.append("- 持续性（历史收据：成为最强驱动后，之后 40 日的解释力 vs 常态）："
                     + " · ".join(f"{L.FACTOR_CN[f]} {L.persistence_label(f)}（{h['fut_r2'] * 100:.0f}% vs "
                                  f"{h['uncond'] * 100:.0f}%，n={h['n']}）" for f, h in hist)
                     + "。「最强驱动」描述近 40 日，不承诺延续")
    return "\n".join(lines).rstrip()


def report(s: Dict) -> str:
    """全文：digest + 全部因子 / 读数 / 描述性状态 / 情景明细 / 组合暴露 / 日历。"""
    drv = s["drivers"]
    L_ = [digest(s), "", "---", "", "### 八因子（40 日窗口；分位 = 自身 3 年）", "",
          "| 因子 | ρ40 | 解释 | 3年分位 | ρ20 | 10 日前解释 | 趋势 |", "|---|---|---|---|---|---|---|"]
    for r in drv["rows"]:
        if r["rho"] is None or r["rho20"] is None:
            L_.append(f"| {r['cn']} | — | — | — | — | — | — |")
            continue
        lag = "—" if r["r2_lag"] is None else f"{r['r2_lag'] * 100:.0f}%"
        L_.append(f"| {r['cn']} | {r['rho']:+.2f} | {r['r2'] * 100:.0f}% | {_ap(r['pct'])} | "
                  f"{r['rho20']:+.2f} | {lag} | {r['trend']} |")
    L_ += ["", "### 描述性状态（研究未通过「风险有效」判据，只作读数）", ""]
    for c in ("S6", "S7", "S9", "S10"):
        x = s["risk"]["states"][c]
        L_.append(f"- {x['name']}：{x['text']}（{'是' if x['on'] else '否' if x['on'] is False else '缺'}）")
    L_ += ["", "### 背离（描述性：预注册只过「之后波动」一项，不预告方向；`" + L.DIVERGENCE_DESIGN + "`）", ""]
    for x in (s.get("divergence") or {}).values():
        mark = "成立" if x["on"] else ("不成立" if x["on"] is False else "缺数据")
        L_.append(f"- {x['name']}：{x['text']} → **{mark}**。历史：{x['finding']}")
    L_.append("- 信用背离（股在高位 + 信用走弱）：已检验不采用 —— 信用走弱单独已是风险灯")
    br = s.get("breadth")
    if br:
        b60 = br["B60"]
        mark = "成立" if b60["on"] else ("不成立" if b60["on"] is False else "缺数据")
        L_ += ["", f"### 广度（描述性，零授权；`{br['design']}`）", "", breadth_line(br),
               f"- 高位 + 窄（B60：SPY ≥0.97×60 日高且 60 日收益 >0，等权相对 60 日变动 ≤3年 p20）→ **{mark}**",
               f"- 历史：{br['finding']}",
               "- 局限：RSP 是等权代理，不是涨跌家数 / 新高新低 / 成分股站上均线比例"]
    L_ += ["", "### 读数", ""]
    for k, rd in s["readings"].items():
        ex = _extreme_txt(k, rd)
        d20 = rd.get("d20")
        if d20 is None:
            chg = "—"
        elif k in ("US10Y", "US3M"):
            chg = f"{d20 * 100:+.0f}bp"
        else:
            base = rd["value"] - d20
            chg = f"{d20 / base * 100:+.1f}%" if base else "—"
        L_.append(f"- {rd['cn']} {rd['value']:.2f}（20 日 {chg}）" + (f" · {ex}" if ex else ""))
    for k, v in (s.get("fred_last") or {}).items():
        if v:
            L_.append(f"- FRED {k} {v[1]:.2f}（{v[0]}）")
    shocks = {sc["factor"]: sc["shock"] for sc in s["forward"]["scenarios"]}
    book = s.get("book")
    if book:
        L_ += ["", f"### 组合暴露（方向名义 ÷ NAV {book['nav']:,.0f}；期权按 delta，一阶近似）", ""]
        for tk, w in sorted(book["weights"].items(), key=lambda kv: -abs(kv[1])):
            L_.append(f"- {tk}：{w * 100:.1f}% NAV")
        for f, ex in book["factors"].items():
            sd = shocks.get(f)
            if sd is None:
                continue
            parts = "、".join(f"{r['ticker']} {_move_txt(r['contrib'] * sd)}" for r in ex["rows"])
            L_.append(f"- {L.FACTOR_CN[f]} {_shock_txt(f, sd)} → 组合 {_move_txt(ex['beta'] * sd)} NAV（{parts}）")
    for sc in s["forward"]["scenarios"]:
        L_ += ["", f"### 情景明细：{L.FACTOR_CN[sc['factor']]} {_shock_txt(sc['factor'], sc['shock'])}"
                   f"（1σ = 近 252 日里 20 日变动的标准差；H4 横截面稳定性 {L.BETA_STABLE[sc['factor']]:.2f}）", ""]
        L_.append("- " + " · ".join(f"{x['ticker']} {_move_txt(x['move'])}" for x in sc["rows"]))
    if s.get("catalysts"):
        L_ += ["", f"### 日历（{CATALYST_DAYS} 天；A/B/C = 量级）", ""]
        for c in s["catalysts"]:
            L_.append(f"- {'★ ' if c.get('hits_driver') else ''}[{c['tier']}] {c['date']} {c['time'] or ''} {c['name']}"
                      f"（{c['channel'] or '未归类'}；{c['source']}）")
    L_ += ["", f"> 研究与裁决：`{L.DESIGN_DOC}`（因子口径 v1.1）。六盏灯 = H1 过（预告波动与回撤，样本外）；"
               "方向 = H2 不成立（唯一例外：宏观主导度高 → 之后偏强，零授权）；主导持续性 = H3 弱且不稳（v1.0 刚过 / v1.1 刚不过；"
               "原油最持续，利率成为最强驱动后通常回落）→「最强驱动」只描述近 40 日；情景数字 = H4 过（日元除外）；"
               "两种模式不因环境改变（H5 只有「宏观主导高 × 突破」过；复核：来自大盘漂移，不接排序）。"]
    return "\n".join(L_)


def overnight_change(price: Optional[float], closes: Sequence[Optional[float]]) -> Optional[float]:
    """隔夜变动 = 最新价 ÷ 上一个**已完成**日线收盘 − 1。最后一根日线还在进行中且已有收盘值（盘中）时，
    上一收盘 = 倒数第二个有效收盘；最后一根收盘为空（期货新交易日刚开，Yahoo 未填）时 = 最后一个有效收盘。"""
    valid = [c for c in closes if c is not None]
    if not price or not valid:
        return None
    if closes and closes[-1] is not None:
        if len(valid) < 2:
            return None
        prev = valid[-2]
    else:
        prev = valid[-1]
    return price / prev - 1 if prev else None


def _live_changes() -> List[Dict]:
    """隔夜变动（盘前简报用）：期货 / 外汇最新价 vs 上一个已完成日线收盘。Yahoo chart，单品种失败跳过。
    布油不列：BZ=F 月末换月时 Yahoo 连续合约切到下一月份，隔夜变动会出现 ±5% 的假跳。"""
    import urllib.parse
    import urllib.request
    syms = [("ES=F", "标普期货"), ("NQ=F", "纳指期货"), ("CL=F", "WTI"), ("ZN=F", "10Y 期货"),
            ("DX-Y.NYB", "美元指数"), ("GC=F", "黄金"), ("JPY=X", "USDJPY")]
    out = []
    for sym, cn in syms:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read())["chart"]["result"][0]
            chg = overnight_change(coerce_float(res["meta"].get("regularMarketPrice")),
                                   res["indicators"]["quote"][0].get("close") or [])
            if chg is not None:
                out.append({"sym": sym, "cn": cn, "chg": chg})
        except Exception as e:                                                  # noqa: BLE001
            print(f"[macro_lens] 隔夜 {sym} 失败：{type(e).__name__}", file=sys.stderr)
    return out


def brief(s: Dict, live: Optional[List[Dict]] = None) -> str:
    """盘前简报四行：档位 / 最强驱动与读法 / 接棒与日历 / 隔夜变动。"""
    drv, tier = s["drivers"], s["risk"]["tier"]
    dom = _row(drv, drv["dominant"]) if drv["dominant"] else None
    chal = _row(drv, drv["challenger"]) if drv["challenger"] else None
    lines = [f"🌐 宏观（{s['asof'][5:]} 收盘读数，判读层）：环境 {tier['tier']}（亮 {tier['count']}/6：{'、'.join(tier['on']) or '无'}）"
             f" · 在交易「{s['story']['theme']}」",
             ("近 40 日最强驱动 " + _driver_line(dom)) if dom else "近 40 日最强驱动：无（没有异常突出的宏观因子）"]
    extra = []
    if chal:
        extra.append(f"接棒 {chal['cn']}（{chal['r2_lag'] * 100:.0f}%→{chal['r2'] * 100:.0f}%）")
    star = [c for c in s.get("catalysts") or [] if c.get("hits_driver") and c.get("tier") == "A"][:3]
    if star:
        extra.append("★日历 " + " · ".join(f"{c['date'][5:]} {c['name']}" for c in star))
    if extra:
        lines.append("；".join(extra))
    live = live if live is not None else _live_changes()
    if live:
        lines.append("隔夜（vs 上一收盘）：" + " · ".join(f"{x['cn']} {x['chg'] * 100:+.1f}%" for x in live))
    return "\n".join(lines)


def week(ledger: Path = MACRO_LEDGER_PATH, sessions: int = 5) -> str:
    """周回顾（周复盘用）：最近 `sessions` 行账本的档位 / 最强驱动 / 剧本轨迹 + 读法翻转 + 上一周最强驱动的对账。"""
    rows = sorted((r for r in iter_jsonl(ledger)), key=lambda r: r.get("asof") or "") if ledger.exists() else []
    if not rows:
        return "## 🌐 宏观透镜周回顾\n账本为空（`trade/intelligence/macro/ledger.jsonl` 尚无记录）"
    cur, prev = rows[-sessions:], rows[-2 * sessions:-sessions]
    cn = L.FACTOR_CN
    lines = [f"## 🌐 宏观透镜周回顾 {cur[0]['asof'][5:]} → {cur[-1]['asof'][5:]}（{len(cur)} 个收盘，判读层零授权）"]
    tiers = [r["tier"]["tier"] for r in cur]
    lit = sorted({x for r in cur for x in r["tier"]["on"]})
    lines.append(f"- 环境：{' → '.join(dict.fromkeys(tiers))}（本周亮过：{'、'.join(lit) or '无'}）")
    doms = [r.get("dominant") for r in cur]
    count = {d: doms.count(d) for d in dict.fromkeys(doms)}
    lines.append("- 最强驱动：" + "；".join(f"{cn.get(d, '无突出因子') if d else '无突出因子'} {n}/{len(cur)} 日"
                                    for d, n in count.items()))
    chal = [r.get("challenger") for r in cur if r.get("challenger")]
    if chal:
        lines.append("- 接棒候选出现过：" + "、".join(cn.get(c, c) for c in dict.fromkeys(chal)))
    themes = list(dict.fromkeys(r.get("theme") for r in cur))
    lines.append("- 在交易：" + " → ".join(themes))
    flips = []
    for f in L.FACTORS:
        signs = [L.sign_of(next((d["rho"] for d in r["drivers"] if d["factor"] == f), None)) for r in cur]
        nz = [x for x in signs if x]
        if len(set(nz)) > 1:
            flips.append(cn[f])
    lines.append("- 读法翻转（|ρ|≥0.30 的符号变过）：" + ("、".join(flips) if flips else "无"))
    if prev:
        last_dom = prev[-1].get("dominant")
        if last_dom:
            now = next((d for d in cur[-1]["drivers"] if d["factor"] == last_dom), {})
            base = L.DOMINANT_HISTORY.get(last_dom, (None, None, None, None))[2]
            r2 = now.get("r2")
            verdict = "—" if r2 is None or base is None else ("✓ 仍高于常态" if r2 > base else "✗ 已回到常态")
            lines.append(f"- 对账：上周末最强驱动 {cn[last_dom]} → 本周末解释力 "
                         f"{'—' if r2 is None else f'{r2 * 100:.0f}%'}（常态 {'—' if base is None else f'{base * 100:.0f}%'}）{verdict}")
    return "\n".join(lines)


def explain(ticker: str, date: str, *, series=None, kdir: Path = KLINE_CACHE_DIR) -> str:
    """某日某票的涨跌里有多少是宏观（前一日读法链上的因子 × 40 日多元 β）——macro-context-check 的归因第 2 步。"""
    series = series if series is not None else load_series()
    fr, ch, t = L.context(series, date)
    if fr["dates"][t] != date:
        return f"{date} 不是有宏观数据的交易日（最近一日 {fr['dates'][t]}）"
    tk = ticker.upper()
    if tk in ch:
        ret = ch[tk]
    else:
        closes = _kline_closes(tk, kdir)
        if not closes:
            return f"{tk} 无日线缓存（{kdir}）"
        ret = L._ln_diff([closes.get(d) for d in fr["dates"]])
    drv_prev = L.drivers(ch, t - 1)
    e = L.explain_day(ret, ch, drv_prev, t)
    if e is None:
        return f"{date} {tk}：数据不足，无法归因"
    parts = " · ".join(f"{L.FACTOR_CN[x['factor']]} {L.display_move(x['factor'], x['move'])} → {x['contrib'] * 100:+.2f}pp"
                       for x in e["parts"])
    return (f"{date} {tk} 实际 {e['actual'] * 100:+.2f}% = 宏观解释 {e['explained'] * 100:+.2f}pp"
            + (f"（{parts}）" if parts else "（前一日没有异常活跃的宏观因子）")
            + f" + 残差 {e['residual'] * 100:+.2f}pp"
            + (f" · 模型 R² {e['r2'] * 100:.0f}%（40 日，止于前一日）" if e.get("r2") is not None else "")
            + f" → **{e['label']}**")


def load_current(path: Path = MACRO_CURRENT_PATH) -> Dict:
    s = read_json(path) or {}   # 缺文件 = None (公开 io_json 约定) → 同「未发布」
    if s.get("schema") != L.SCHEMA:
        raise SystemExit(f"{path} 不是 {L.SCHEMA}（先跑 --update）")
    return s


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="宏观透镜：发布 / 推送 / 简报（判读层，零授权）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true", help="刷新数据 → 读数 → 写 current / ledger / 全文")
    g.add_argument("--digest", action="store_true", help="推送段（读 current.json）")
    g.add_argument("--brief", action="store_true", help="盘前简报四行（current.json + 隔夜期货）")
    g.add_argument("--report", action="store_true", help="全文 Markdown")
    g.add_argument("--week", action="store_true", help="周回顾（读账本，周复盘用）")
    g.add_argument("--explain", metavar="TICKER", help="某日涨跌的宏观归因（配 --date）")
    g.add_argument("--json", action="store_true")
    ap.add_argument("--asof", help="截断到该交易日（复盘；只配合 --update --no-publish / --json 用）")
    ap.add_argument("--date", help="--explain 的交易日 YYYY-MM-DD（缺省 = 最近一日）")
    ap.add_argument("--no-refresh", action="store_true", help="--update 不拉数据，只用缓存")
    ap.add_argument("--no-publish", action="store_true", help="--update 只打印，不写文件")
    ap.add_argument("--session", help="--update 的本交易日（定时任务传 due 返回的 session）：读数 asof ≠ 它 → 不发布，退出码 3")
    a = ap.parse_args(argv)
    if a.update or a.json and a.asof:
        failed = [] if a.no_refresh else refresh()
        s = build(a.asof, refresh_failed=failed)
        if a.json:
            print(json.dumps(s, ensure_ascii=False, indent=1))
            return 0
        if a.no_publish or a.asof:
            print(digest(s))
            return 0
        if a.session and s["asof"] != a.session:
            print(f"⚠️ 宏观透镜未发布：本交易日 {a.session} 的日线没取到（SPY 只到 {s['asof']}），"
                  f"current.json / 账本 / 全文均未改动" + (f"；刷新失败 {','.join(failed)}" if failed else ""))
            return 3
        res = publish(s)
        gap = gap_line(s)
        print(f"macro_lens {s['asof']}: {s['risk']['tier']['tier']} · 最强驱动 {s['drivers']['dominant'] or '无'} · "
              f"账本{'+1' if res['ledger_appended'] else '已有'} → {res['current']}" + (f"；{gap}" if gap else ""))
        return 0
    if a.week:
        print(week())
        return 0
    if a.explain:
        series = load_series()
        day = a.date or series["SPY"][-1][0]
        print(explain(a.explain, day, series=series))
        return 0
    s = load_current()
    if a.digest:
        print(digest(s))
    elif a.brief:
        print(brief(s))
    elif a.report:
        print(report(s))
    else:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
