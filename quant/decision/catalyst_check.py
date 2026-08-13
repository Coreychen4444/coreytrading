#!/usr/bin/env python3
"""catalyst_check.py — 异动归因的**机械校验 + 代码渲染**, 不是散文。

为什么存在 (用户  复盘  简报):
    简报写「触发源 = TSMC 周四盘后财报 → AI capex 可持续性恐慌」。核实后:
      · TSMC Q2 财报是 **7/16**, 不是 7/23 —— 差一周
      · 7/23 盘后真正的财报是 **INTC**(大幅 beat, 盘后 +12~13%, 7/24 尽数回吐 -7.9%)
      · NBIS 那根 -15% 被安上了 7/16 那根 -13.9% 的理由 (内部人 + 自家 capex),
        而 tape 在 7/21 已经 +18.8% 把那套叙事**收复并超过跌前**—— 用一个被
        tape 否掉的 catalyst 解释 8 天后的新跌幅
      · 结论写成「= 已知 risk #3, 非新破位」—— 拿跌幅去卡片 key_risk 列表里
        找了个能对上的条目就宣布"已知"。风险列表有 6 条, 任何坏日子都能匹配 →
        **后验永远不动**

    数字层 (position_cards / exit_guards) 是代码渲染的, 消息层是模型自由散文:
    无日期绑定、无来源、无回测。同 data_discipline 叙述层三形状。

四道机械检查 (全部只用本地 kline, 不联网):
  1. 日期绑定   catalyst 日期必须落在 [异动日-1 交易日, 异动日]。更早 → 自动
                降级为「背景」并**从触发源里剔除**
  2. tape 否定   catalyst 日 → 异动日之间价格已完全收复并超过跌前 → REFUTED_BY_TAPE,
                **硬禁止**当解释 (NBIS 7/16→7/21 +18.8% 即此)
  3. 板块残差   |个股| vs 板块中位, 超 1.5× → 输出"板块解释不了这部分"
  4. 空集允许   无 catalyst 存活 → 渲染「⚠️ 触发源未知」。**这是合法输出**——
                散文永远能编出一个理由, 这才是最危险的部分

归因落 market/events/catalysts.jsonl, 事后可回查"当时说是什么原因, 后来对不对"。

CLI:
    python3 quant/decision/catalyst_check.py NBIS --date  \
      --catalyst :INTC 财报 sell-the-news:https://...:sector \
      --catalyst :chairman 内部人抛售:https://...:stock  [--log] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import statistics as stats
from typing import Dict, List, Optional

from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR  # noqa: E402
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402

CATALYSTS_PATH = KLINE_CACHE_DIR.parent / "events/catalysts.jsonl"
SECTOR_PEERS = ["NVDA", "AMD", "MU", "INTC", "MRVL", "LITE", "AAOI", "AMKR", "QCOM", "TSM"]
PARAMS = {
    "max_lag_sessions": 1,      # catalyst 最多早异动日 1 个交易日
    "residual_mult": 1.5,       # |个股| > 1.5× 板块中位 → 未解释残差
    "min_peers": 3,
    "scopes": {"stock", "sector", "macro"},
}


def closes(ticker: str) -> List[tuple]:
    p = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    out = []
    for r in iter_jsonl(p):
        d, c = str(r.get("time", ""))[:10], coerce_float(r.get("close"))
        if d and c:
            out.append((d, c))
    return sorted(out)


def move_on(ticker: str, day: str) -> Optional[Dict]:
    """异动日的真实收盘变动。**用 kline 算, 不接受调用方口报的百分比**——
    7/27 简报里 "NBIS -10.8%" 就是盘前快照比错基准的产物。"""
    rows = closes(ticker)
    idx = next((i for i, (d, _) in enumerate(rows) if d == day), None)
    if idx is None or idx == 0:
        return None
    prev_d, prev_c = rows[idx - 1]
    return {"date": day, "close": rows[idx][1], "prev_date": prev_d, "prev_close": prev_c,
            "change_pct": round((rows[idx][1] / prev_c - 1) * 100, 2)}


def sessions_between(ticker: str, a: str, b: str) -> Optional[int]:
    """a→b 之间隔了几个交易日 (按该 ticker 的 kline 日历数, 不查假日表)。"""
    days = [d for d, _ in closes(ticker)]
    if a > b:
        return None
    before = [d for d in days if d <= a]
    if not before:
        return None                      # catalyst 早于缓存范围
    ia = len(before) - 1
    ib = next((i for i, d in enumerate(days) if d == b), None)
    return None if ib is None else ib - ia


def refuted_by_tape(ticker: str, cat_date: str, move_date: str) -> Optional[Dict]:
    """catalyst 日之后、异动日之前, 价格是否已完全收复并超过 catalyst 前的水平。

    收复了 = 市场已经把这套叙事定价回去。再拿它解释后来的新跌幅, 逻辑上不成立。
    源案例 NBIS: 7/16 -13.9% (内部人+自家 capex) → 7/21 收 216.92, 高于跌前
    7/15 的 199.51 → 7/24 那根 -15% 与这套理由无关。

    两条防误判 (首版就踩了):
      · **收复窗口从 catalyst 日之后起算**。首版把当日也算进去, 于是 T-1 的
        盘后财报 (INTC 7/23 AMC) 被自己那天的收盘"收复"掉 —— 而消息在收盘后才出。
      · **至少隔 2 个交易日才谈收复**。隔 1 天根本没有收复的时间, 判 refuted
        是把一个合法解释扔掉。
      · 收复基准取 max(前一日收盘, catalyst 日收盘) —— 不知道消息是盘前还是
        盘后时取高者 = 更难判 refuted。误判 refuted 的代价比漏判大。
    """
    rows = closes(ticker)
    idx = {d: i for i, (d, _) in enumerate(rows)}
    ic, im = idx.get(cat_date), idx.get(move_date)
    if ic is None or im is None or ic == 0 or im - ic < 2:
        return None
    pre = max(rows[ic - 1][1], rows[ic][1])
    window = rows[ic + 1:im]                     # 严格在 catalyst 日之后, 不含异动日
    if not window:
        return None
    peak = max(c for _, c in window)
    peak_d = next(d for d, c in window if c == peak)
    if peak > pre:
        return {"pre_close": pre, "peak": peak, "peak_date": peak_d,
                "recovered_pct": round((peak / pre - 1) * 100, 2)}
    return None


def sector_residual(ticker: str, day: str) -> Optional[Dict]:
    peers = []
    for t in SECTOR_PEERS:
        if t == ticker:
            continue
        m = move_on(t, day)
        if m:
            peers.append((t, m["change_pct"]))
    if len(peers) < PARAMS["min_peers"]:
        return None
    med = stats.median([c for _, c in peers])
    me = move_on(ticker, day)
    if not me or abs(med) < 1e-9:
        return None
    ratio = abs(me["change_pct"]) / abs(med)
    return {"ticker_pct": me["change_pct"], "peer_median_pct": round(med, 2),
            "n_peers": len(peers), "ratio": round(ratio, 2),
            "unexplained": ratio > PARAMS["residual_mult"],
            "peers": sorted(peers, key=lambda x: x[1])}


def parse_catalyst(raw: str) -> Dict:
    """`YYYY-MM-DD:headline:url:scope` — url / scope 可省。"""
    parts = raw.split(":", 1)
    date = parts[0].strip()
    rest = parts[1] if len(parts) > 1 else ""
    # url 里有 ':' —— 从右侧按 scope 关键字切
    scope = None
    for s in PARAMS["scopes"]:
        if rest.endswith(f":{s}"):
            scope, rest = s, rest[: -len(s) - 1]
            break
    url = None
    for marker in ("https://", "http://"):
        i = rest.find(f":{marker}")
        if i >= 0:
            url, rest = rest[i + 1:], rest[:i]
            break
    return {"date": date, "headline": rest.strip(), "url": url, "scope": scope}


def check(ticker: str, day: str, catalysts: List[Dict]) -> Dict:
    move = move_on(ticker, day)
    out: Dict = {"ticker": ticker, "date": day, "move": move,
                 "residual": sector_residual(ticker, day) if move else None,
                 "triggers": [], "background": [], "refuted": [], "invalid": []}
    if not move:
        out["error"] = f"{ticker} 无 {day} 的已完成日线 —— 盘中或非交易日? (kline 只存已完成收盘)"
        return out

    for c in catalysts:
        rec = dict(c)
        if not c.get("scope") or c["scope"] not in PARAMS["scopes"]:
            rec["reason"] = f"scope 缺失或非法 (需 {'/'.join(sorted(PARAMS['scopes']))})"
            out["invalid"].append(rec)
            continue
        if not c.get("url"):
            rec["reason"] = "无来源 URL —— 归因必须可回查, 否则和散文没区别"
            out["invalid"].append(rec)
            continue
        lag = sessions_between(ticker, c["date"], day)
        if lag is None:
            rec["reason"] = "日期不在 kline 范围内, 无法校验"
            out["invalid"].append(rec)
            continue
        rec["lag_sessions"] = lag
        ref = refuted_by_tape(ticker, c["date"], day)
        if ref:
            rec["refutation"] = ref
            rec["reason"] = (f"REFUTED_BY_TAPE — {c['date']} 后价格于 {ref['peak_date']} "
                             f"收 {ref['peak']:g}, 已收复并超过跌前 {ref['pre_close']:g} "
                             f"({ref['recovered_pct']:+.1f}%)。市场已把这套叙事定价回去, "
                             f"不能再拿它解释 {day}")
            out["refuted"].append(rec)
        elif lag > PARAMS["max_lag_sessions"]:
            rec["reason"] = (f"早于异动日 {lag} 个交易日 (上限 {PARAMS['max_lag_sessions']}) "
                             f"→ 降级为**背景**, 不进触发源")
            out["background"].append(rec)
        else:
            out["triggers"].append(rec)
    return out


def render(r: Dict) -> str:
    L = [f"📌 异动归因 {r['ticker']} {r['date']}"]
    if r.get("error"):
        return "\n".join(L + [f"   ⛔ {r['error']}"])
    m = r["move"]
    L.append(f"   实际: 收 {m['close']:g} ({m['change_pct']:+.2f}% vs {m['prev_date']} "
             f"{m['prev_close']:g})   ← kline 算的, 非口报")

    res = r.get("residual")
    if res:
        tag = "⚠️ " if res["unexplained"] else ""
        L.append(f"   {tag}板块: 中位 {res['peer_median_pct']:+.2f}% (n={res['n_peers']}) "
                 f"→ 本票 {res['ratio']:g}×"
                 + ("  **超出部分板块解释不了**" if res["unexplained"] else ""))

    if r["triggers"]:
        L.append("   ✅ 触发源:")
        for c in r["triggers"]:
            L.append(f"      · [{c['date']}, T-{c['lag_sessions']}, {c['scope']}] {c['headline']}")
            L.append(f"        {c['url']}")
        # 有触发源 ≠ 解释完了。板块级 catalyst 只解释板块那部分, 超额是另一回事 ——
        # 不写这句, "✅ 触发源" 会被当成完整解释 (7/27 简报正是这么读的)。
        if res and res["unexplained"] and all(c["scope"] != "stock" for c in r["triggers"]):
            L.append(f"      ⚠️ 但触发源全是 {'/'.join(sorted({c['scope'] for c in r['triggers']}))} 级 —— "
                     f"只解释了板块那 {res['peer_median_pct']:+.2f}%; 本票 {res['ticker_pct']:+.2f}% "
                     f"的超出部分**仍无个股级解释**, 按未解释处理")
    else:
        L.append("   ⚠️ **触发源未知**— 无 catalyst 通过日期绑定与 tape 检查。")
        L.append("      不要用卡片 key_risk 里能对上的条目补这个空 —— 顺序只能是"
                 "「先查发生了什么」→「再看是否落在已知 risk」。匹配不上就是未解释。")

    for label, key in (("📎 背景 (非触发源)", "background"),
                       ("⛔ 被 tape 否定", "refuted"),
                       ("🚫 不合规", "invalid")):
        if r[key]:
            L.append(f"   {label}:")
            for c in r[key]:
                L.append(f"      · [{c['date']}] {c['headline']}")
                L.append(f"        {c['reason']}")
    return "\n".join(L)


def log(r: Dict) -> None:
    CATALYSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(CATALYSTS_PATH, {
        "logged_at": now_et_iso(), "ticker": r["ticker"], "date": r["date"],
        "change_pct": (r.get("move") or {}).get("change_pct"),
        "residual_ratio": (r.get("residual") or {}).get("ratio"),
        "unexplained": bool((r.get("residual") or {}).get("unexplained")),
        "triggers": [{k: c.get(k) for k in ("date", "headline", "url", "scope")}
                     for c in r["triggers"]],
        "background": [c.get("headline") for c in r["background"]],
        "refuted": [c.get("headline") for c in r["refuted"]],
        "trigger_count": len(r["triggers"]),
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="异动归因机械校验")
    ap.add_argument("ticker")
    ap.add_argument("--date", default=None, help="异动日 (ET, 默认最近一个完成交易日)")
    ap.add_argument("--catalyst", action="append", default=[],
                    help="YYYY-MM-DD:标题:URL:scope(stock|sector|macro), 可重复")
    ap.add_argument("--log", action="store_true", help="落 market/events/catalysts.jsonl")
    ap.add_argument("--tape", action="store_true",
                    help="附 60m 量价下沉判读 (唯一联网步骤, OpenD 不可达自动降级)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    tk = a.ticker.upper()
    day = a.date
    if not day:
        rows = closes(tk)
        day = rows[-1][0] if rows else et_today()
    r = check(tk, day, [parse_catalyst(c) for c in a.catalyst])
    if a.log and not r.get("error"):
        log(r)
    tape_txt = None
    if a.tape:
        try:
            from quant.decision.tape_check import probe  # 同层软依赖, 默认关闭
            tape_txt = probe(tk, r.get("date") or day)
        except Exception as e:  # 四道本地检查不因联网步骤失败而阻塞
            tape_txt = f"⚠️ 60m tape 不可用: {e}"
    if a.json:
        if tape_txt is not None:
            r["tape_60m"] = tape_txt
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        out = render(r)
        print(out + ("\n" + tape_txt if tape_txt else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
