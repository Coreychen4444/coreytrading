#!/usr/bin/env python3
"""earnings_calendar.py — 前视财报日历 + per-ticker 缩仓比例 → 单文件缓存。

为什么存在 (`shock_budget_wiring_todo.md` §B 的前置): earnings_window 路 A 已由
用户  批准, 但当时 `market/earnings_deep/` 只是**历史存档, 没有前视
日历**—— 一条无法在 live 触发的规则等于不存在。

 查证: OpenD 的 `get_financials_earnings_price_history` **带未来一期**
(NVDA 返回 `pub_trading_day_str=` / `close_price=null`), 还附
`predict_vola_ratio_newest` = 期权隐含波动幅度。前置解除, 于是有了这个脚本。

产出 `market/earnings_calendar.json` (入 git, 小):
    {"as_of", "tickers": {T: {next_reaction_day, pub_day, pub_type, implied_move_pct,
                              x_earn, n_prior, x_source,
                              last_print: {pub_day, pub_type, reaction_day, ...}}}}

`last_print` = **已落地那一期的存根**, 由 `merge_last_print` 逐轮粘住 ()。
前视条目在反应日**次日**就滚成下一季 (`pick_upcoming` 的 `rday < today`), 而
`core.earnings.cards_stale_after_print` 的重画 SLA 告警要一直吵到卡真被重画 ——
不粘住它就是一条只活一天的告警。

**反应日不是公布日**: AMC(pub_type=2) 公布 → 反应日 = 下一个交易日; BMO(=1) 当日。
历史行的 `trading_day_str` 已经是反应日, 用它反推口径 (对得上才敢推未来那一期)。

X_earn = C 收缩臂 (`core/earnings` 的常量, 与 research/earnings_window.py 同一组):
λ·单票 p98 + (1−λ)·池级 p90, λ=n/(n+10); 先验 <8 → 池级。ATR 用 `klines_deep`
(不入 git, 回测资产) —— **缺了就回退池级并写进 `x_source`**, 不静默。

CLI:
    .venv/moomoo/bin/python3 quant/data/earnings_calendar.py [--json] [--tickers ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)
from quant.core import technical  # noqa: E402
from quant.core.earnings import (# noqa: E402
    MIN_PRIOR, POOL_X_EARN, SHRINK_N0, shrink_ratio)
from quant.core.holdings import fund_tickers, is_candidate, is_traded  # noqa: E402  (候选/参考票/基金票判据单一真源)
from quant.core.io_json import atomic_write_json, iter_jsonl, read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import (# noqa: E402
    EARNINGS_CALENDAR_PATH, EARNINGS_DEEP_DIR, KLINE_CACHE_DIR, KLINE_DEEP_DIR,
    POSITIONS_PATH, WATCHLIST_PATH, add_openapi_path)
from quant.core.timeutil import et_today  # noqa: E402

ATR_PERIOD = 20
# 公布日已过、但反应日可能仍在今天或未来: AMC 是 T+1, 跨周末最多顺延 3 个自然日。
# 给 5 天余量; 更早的 pending 行 = vendor 没回填收盘的历史期, 由 `rday < today` 逐行剔掉。
REACTION_LOOKBACK_DAYS = 5
TICKER_Q = 0.98              # 单票分位 (B/C 臂), 与 research 同


def _quantile(vals: List[float], q: float) -> Optional[float]:
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def _deep_bars(ticker: str) -> List[dict]:
    """深度日线优先, 回落常规缓存 (只够近一年 → 事件数通常 <8, 自动走池级)。"""
    for d in (KLINE_DEEP_DIR, KLINE_CACHE_DIR):
        p = d / f"{ticker}.jsonl"
        if p.exists():
            rows = list(iter_jsonl(p))
            if rows:
                return rows
    return []


def event_drops(ticker: str, reaction_days: List[str]) -> List[float]:
    """历史财报反应日的**跌幅**(ATR20 归一, 跌为正)。ATR 取事件前一日。"""
    bars = _deep_bars(ticker)
    if len(bars) < ATR_PERIOD + 30 or not reaction_days:
        return []
    closes = [coerce_float(b.get("close")) for b in bars]
    atr = technical.atr(bars, ATR_PERIOD)
    idx = {str(b.get("time"))[:10]: i for i, b in enumerate(bars)}
    out = []
    for d in reaction_days:
        i = idx.get(d)
        if not i or i == 0 or not atr[i - 1] or not closes[i] or not closes[i - 1]:
            continue
        out.append(-(closes[i] - closes[i - 1]) / atr[i - 1])
    return out


def estimate_x_earn(drops: List[float]) -> tuple:
    """C 收缩臂 → (x_earn, n, source)。先验 <8 = 池级, 不假装知道。"""
    n = len(drops)
    if n < MIN_PRIOR:
        return POOL_X_EARN, n, f"pool_p90 (先验 {n} < {MIN_PRIOR})"
    tq = _quantile(drops, TICKER_Q) or POOL_X_EARN
    lam = n / (n + SHRINK_N0)
    return lam * tq + (1 - lam) * POOL_X_EARN, n, f"C_shrink λ={lam:.2f}"


def archive_reaction_days(ticker: str) -> List[str]:
    p = EARNINGS_DEEP_DIR / f"{ticker}.json"
    if not p.exists():
        return []                      # 无存档 = 无先验 → estimate 自动回退池级
    d = read_json(p) or {}
    return sorted({str(r.get("trading_day_str"))[:10]
                   for r in (d.get("data") or []) if r.get("trading_day_str")})


def card_tickers() -> List[str]:
    """**在场**的正股 ticker (期权归标的)。财报日历只对正股有意义。

    在场 = 持仓 (broker.qty≠0) 或 watchlist watching。平仓残卡不查 —— 它们既不会
    被缩仓, 每轮还各吃一次 API 配额并留一行错 (DRAM 是 ETN, 直接报"只支持股票")。
    """
    out = set()
    pos = (read_json(POSITIONS_PATH) or {}).get("tickers") or {}
    wl = (read_json(WATCHLIST_PATH) or {}).get("tickers") or {}
    # 基金票 (ETF): 交易, 但没有财报日 —— 接口直接拒 ("only supports equities")。按标的
    # 名字查: 期权卡 body 不带基金章, 章在 underlying 那张卡上。
    funds = fund_tickers({"tickers": pos}, {"tickers": wl})
    for tk, body in list(pos.items()) + list(wl.items()):
        occ = parse_occ(tk)
        base = occ["underlying"] if occ else tk
        if not base.isalpha() or base in funds:
            continue
        if not is_traded(body):        # 参考票: 不交易, 无财报语义
            continue
        broker = body.get("broker")
        live = bool(coerce_float((broker or {}).get("qty")))
        # watching 判据不能碰 broker 有无: watchlist 卡  起携带报价快照
        # (broker block, qty 恒 0) —— 旧判据 `broker is None` 在快照上线当天把
        # watchlist 腿静默清空, 日历只剩 positions, entry 侧 T+0 缩仓与财报提示
        # 全部失效 (COHR  print 前夜 用户 抓漏)。未持仓 + watching 即在场;
        # Today 组成员 ⊆ 持仓∪watchlist, 覆盖两文件即覆盖 Today。
        # 候选判据 = holdings.is_candidate (status=watching 且 moomoo_listed≠false): 移出自选分组
        # 只翻 moomoo_listed 不改 status, 只看 status 会把墓碑卡的财报排进「下周财报」并白吃
        # API 配额 ( 周复盘幽灵标的案, 判别式收拢第 N 处)
        watching = (not live) and is_candidate(body)
        if live or watching:
            out.add(base)
    return sorted(out)


def _next_trading_day(ctx, day: str) -> Optional[str]:
    """公布日之后的下一个交易日 (AMC 的反应日)。查不到 → 顺延到下个工作日。"""
    try:
        from moomoo import Market, RET_OK
        d0 = date.fromisoformat(day)
        ret, df = ctx.request_trading_days(
            market=Market.US, start=(d0 + timedelta(days=1)).isoformat(),
            end=(d0 + timedelta(days=10)).isoformat())
        if ret == RET_OK and df:
            days = sorted(str(r.get("time")) for r in df if r.get("time"))
            if days:
                return days[0]
    except Exception:                                              # noqa: BLE001
        pass
    d = date.fromisoformat(day) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def pick_upcoming(rows: List[dict], today: str, next_trading_day) -> Optional[Dict]:
    """行集 → 前视那一期 `{pub_day, pub_type, next_reaction_day, implied_move_pct}`。

    🚨 判据是「**反应日还没过**」, 不是「公布日在未来」(修)。

    AMC 票的反应日在公布日**之后**(T+1), 而缩仓恰恰要在**反应日**那天生效。
    旧判据 `pub_day > today` 严格大于 → 公布日当天整条腿就从日历里消失, 且此后
    再也回不来 (pub_day 只会越来越旧) ⇒ **AMC 票的 T+0 缩仓永远不会触发**。
    源案例  LITE: 8/10 的日历还写着 pub 8/11 AMC / IM 13.5%, 8/11 当天
    整条腿没了 —— 而它的反应日 8/12 正是该缩仓的那天。同一个洞轮着咬每一只 AMC 票
    (次日轮到 COHR)。`core.earnings.event_state` 只认 `next_reaction_day == today`,
    读不到的那天它连"缺失"都不会报 —— 因为字段本身合法地不存在。

    `next_trading_day(pub) -> str` 注入以便测试 (live 走 OpenD 交易日历)。
    """
    lookback = (date.fromisoformat(today) - timedelta(days=REACTION_LOOKBACK_DAYS)).isoformat()
    pending = sorted((r for r in rows
                      if str(r.get("pub_trading_day_str") or "")[:10] >= lookback
                      and coerce_float(r.get("close_price")) is None),
                     key=lambda r: str(r.get("pub_trading_day_str")))
    for r in pending:
        pub = str(r.get("pub_trading_day_str"))[:10]
        ptype = int(coerce_float(r.get("pub_type")) or 0)
        # BMO(1) 当日反应; AMC(2)/盘中(3)/未知(0) 一律算次日 —— 未知按 AMC 处理是
        # 保守方向 (晚一天缩仓 ≠ 少缩, 只是窗口右移一格)。
        rday = pub if ptype == 1 else next_trading_day(pub)
        if not rday or rday < today:
            continue        # 反应日已过 = vendor 还没回填收盘的历史期, 不是前视那一期
        out = {"pub_day": pub, "pub_type": ptype, "next_reaction_day": rday}
        im = coerce_float(r.get("predict_vola_ratio_newest"))
        if im:
            out["implied_move_pct"] = round(im, 2)
        return out
    return None


def _landed_stub(entry: Optional[Dict], today: str) -> Optional[Dict]:
    """条目里那一期 print 若**反应日已到**, 归一成 `last_print` 存根。"""
    entry = entry or {}
    rday = str(entry.get("next_reaction_day") or "")[:10]
    if not rday or rday > today:
        return None
    stub = {"pub_day": entry.get("pub_day"), "pub_type": entry.get("pub_type"),
            "reaction_day": rday,
            "implied_move_pct": entry.get("implied_move_pct")}
    return {k: v for k, v in stub.items() if v is not None}


def merge_last_print(out: Dict, prev: Optional[Dict], today: str,
                     names: List[str]) -> Dict:
    """把**已落地**那一期粘进 `last_print`, 三个来源取反应日最新的一个。

    来源: 上一版粘住的 `last_print` / 上一版前视条目 (反应日已到) / 本轮新条目
    (BMO 当日 = 落地即前视)。取 max 而不是覆盖 —— 单向只能变新, 一次抓错不会
    把已知的 print 倒退回去。

    `setdefault` 让**本轮拉失败**的票也保住存根: 拉失败走 `continue`, 该票整条
    从 `out["tickers"]` 消失, 存根就跟着没了 —— 那正是"一次网络错误清掉一条
    告警"的静默失效形状。补回来的存根只有 `last_print`, 没有 `next_reaction_day`,
    所以 `event_state` 照旧 active=False, 不会假装知道缩仓。

    只在 `names` 范围内补, 不复活本轮没查的票 (`--tickers` 子集调用时)。
    """
    prev_t = ((prev or {}).get("tickers") or {})
    for tk in names:
        cands = [c for c in ((prev_t.get(tk) or {}).get("last_print"),
                             _landed_stub(prev_t.get(tk), today),
                             _landed_stub(out["tickers"].get(tk), today))
                 if isinstance(c, dict) and c.get("reaction_day")]
        if cands:
            out["tickers"].setdefault(tk, {})["last_print"] = max(
                cands, key=lambda c: str(c["reaction_day"]))
    return out


def build(tickers: Optional[List[str]] = None) -> Dict:
    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK  # noqa: E402

    today = et_today()
    names = tickers or card_tickers()
    out: Dict = {"as_of": today, "tickers": {}, "errors": []}
    ctx = None
    try:
        ctx = create_quote_context()
        for i, tk in enumerate(names):
            # 30 req/30s 硬限 (首版实测被拒: 一票一次 + trading_days 各一次)。
            # 留一半余量, 别让配额耗尽变成"某几只票静默没有财报日历"。
            if i and i % 10 == 0:
                time.sleep(12)
            try:
                ret, df = ctx.get_financials_earnings_price_history(f"US.{tk}")
            except Exception as e:                                 # noqa: BLE001
                out["errors"].append(f"{tk}: {type(e).__name__}: {e}")
                continue
            if ret != RET_OK or df is None or not hasattr(df, "to_dict"):
                out["errors"].append(f"{tk}: {df}")
                continue
            rows = df.to_dict("records")
            # 前视那一期 = 反应日还没过、且没有反应日收盘。同一公布日会展开成多行
            # offset, 取第一行的 period 元数据即可。
            # `close_price` 空的形态有三种: None / "" / pandas 的 NaN。
            # 只判前两种 → 未来那一期永远筛不出来 (首版实测 "0 有前视日期")。
            drops = event_drops(tk, archive_reaction_days(tk))
            x_earn, n_prior, src = estimate_x_earn(drops)
            entry = {"x_earn": round(x_earn, 3), "n_prior": n_prior, "x_source": src}
            entry.update(pick_upcoming(rows, today,
                                       lambda d: _next_trading_day(ctx, d)) or {})
            out["tickers"][tk] = entry
    finally:
        if ctx is not None:
            safe_close(ctx)
    return merge_last_print(out, read_json(EARNINGS_CALENDAR_PATH), today, names)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="前视财报日历 + 缩仓比例 → 缓存")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    a = ap.parse_args(argv)
    doc = build([t.upper() for t in a.tickers] if a.tickers else None)
    if not a.dry_run:
        atomic_write_json(EARNINGS_CALENDAR_PATH, doc)
    if a.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        upcoming = {t: v for t, v in doc["tickers"].items() if v.get("next_reaction_day")}
        print(f"财报日历 {doc['as_of']}: {len(doc['tickers'])} 票, {len(upcoming)} 有前视日期")
        for t, v in sorted(upcoming.items(), key=lambda kv: kv[1]["next_reaction_day"]):
            # 缩仓比例必须**在这里**渲染: 简报 §2 财报行要带它 (反应日当天唯一可
            # 操作的数字)。不打印 = 播报者得自己心算 ×0.7 —— 那正是"数字必须算出来
            # 不是抄的"要挡的形状 (用户 , moomoo z1 备注同案)。
            print(f"  {t} 反应日 {v['next_reaction_day']} | 缩仓 ×"
                  f"{shrink_ratio(v.get('x_earn')):.2f} | X_earn {v['x_earn']}N "
                  f"({v['x_source']})" + (f" | 隐含 ±{v['implied_move_pct']}%"
                                          if v.get("implied_move_pct") else ""))
        for e in doc["errors"]:
            print(f"  ✗ {e}")
    return 0 if not doc["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
