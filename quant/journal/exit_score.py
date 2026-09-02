"""结构性退出的自动评分 → scores.jsonl ( 用户 立: NBIS 案「评分
nbis 结构性退出应该要做到自动评分」)。

为什么存在: close_reconcile 把平仓收尾自动化后, 结构性退出唯一剩下
的人工动作是 `annotate_event --score` —— 而本仓的定律是**手工步骤 = 这一步实际
上不存在** (NBIS 8/24 破 stop 当晚 20:02 ET 全平, 收尾/事件流/降级三个模块都干
了活, 评分在次日简报里仍是一行「⏳ 仍缺一件」待办)。结构性退出的评分恰好是
机械可判的: 钟几时响 (降级日) / 手几时动 (closed_at) / 比例对不对 (close_reconcile
只处理全平) —— 全在 state 里, 没有要人填的判断。

判据 (对账式幂等, 与 close_reconcile / unresolved_demotions 同哲学 —— 漏一轮
下轮补上, 不加状态字段; 它是 `core.structure.unresolved_demotions` 的**对偶**:
那边 = down 在案 + 无动作 → 每日点名到动作为止; 这边 = down 在案 + 动作已
发生 → 记分闭环):
  ① positions 卡 status == closed 且 exit_info.closed_at 在 (close_reconcile 落的)
  ② `latest_demotion(agent)` 在案 (regime_history 最新一条 down) —— 止盈清仓
     没有它, 不进本模块, 评分归人 (§8 新成交流程照旧)
  ③ closed_date(ET) ≥ 降级日 —— 平仓早于降级 = 不是钟响驱动
  ④ scores.jsonl 里该 ticker 自降级日起无 phase=exit 行 (人工已评 / 上轮已评
     都消音; 与 unresolved_demotions 判据 ④ 同款「落库即消音」)

评分 = 纯时延档 (D+N 由 kline 完成 bar 口径算, `trading_days_after` 同源):
  D+0 → A   钟响当日执行, 含盘后 (COHR 8/19 范本, 零附加税)
  D+1 → B   次交易日 —— 8/22 裁决要求当日 (盘后可成交即当晚), 若当晚流动性
            不可行属最快可行执行, notes 声明可改判
  D+2+ → C  exit_overdue 形状 (override 三用三付费: AAOI −$98 / INTC −$76 /
            GOOGL −$725), 规则外评 C 起步
机械分**只评 时延+全平比例**两维 —— 逻辑/checklist 归人; 防守向不适用「错+好
D」陷阱 (AAOI 8/21 A- 先例); 改判权在 用户 (MRVL 8/22 C→B 先例), source 字段
标 `exit_score auto` 让改判可辨认。kline 缺失 → 不评只报 (不给错分)。

依赖: core。pipeline 的 `exit_score` 步 (reconcile 之后、watchlist_sync 之前 ——
促升覆盖 closed 卡前评完) 两模式跑; --read-only 降级 --dry-run。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from typing import List, Optional

from quant.core import env  # noqa: F401  (.env → os.environ)
from quant.core.io_json import append_jsonl, iter_jsonl
from quant.core.num import coerce_float
from quant.core.paths import KLINE_CACHE_DIR, POSITIONS_PATH, SCORES_PATH
from quant.core.structure import latest_demotion, trading_days_after
from quant.core.timeutil import parse_ts

SOURCE = "exit_score auto"
# 生效日边界: 只评 closed_date ≥ 此日的退出, 历史不回填。双理由:
# ① 裁决向前有效 (用户 逐字「8/20 那个 B 不追改」) —— INTC 8/21 的
#   D+2 迟到发生在 override 退役裁决**之前**, 回填 C = 追溯适用;
# ② COHR 8/19 已有同 ref 的 episode 复盘行 ( C) —— 已被人复盘过的
#   历史退出再补机械行 = 重复记账。 = NBIS (首笔 live, 用户 点名)。
EFFECTIVE_FROM = "2026-08-24"


def _delay_days(ticker: str, demote_date: str, closed_date: str,
                bars_of=None) -> Optional[int]:
    """D+N: 降级日与执行日之间错过了几个收盘。

    = #{完成 bar: demote < d < closed} + (1 if closed != demote else 0)。
    同日 (含当晚盘后) = 0; 次交易日 = 1 (中间无 bar, 周末不计迟);
    盘中执行日自己的 bar 未完成不在缓存 —— 严格 `< closed` 使它天然不计。
    closed != demote 且无 bar 序列 → None (算不出, 不猜)。
    """
    if closed_date == demote_date:
        return 0
    rows = (bars_of or _cached_bars)(ticker)
    if not rows:
        return None
    dates = [str(r.get("time", ""))[:10] for r in rows]
    between = trading_days_after([d for d in dates if d < closed_date], demote_date)
    return between + 1


def _cached_bars(ticker: str) -> List[dict]:
    return list(iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl"))


def _score_of(delay: int) -> str:
    return "A" if delay == 0 else ("B" if delay == 1 else "C")


def _notes(tk: str, delay: int, demote: dict, exit_info: dict) -> str:
    lvl = coerce_float(demote.get("level"))
    px = coerce_float(exit_info.get("exit_price"))
    pnl = coerce_float(exit_info.get("realized_pnl_usd"))
    when = str(exit_info.get("closed_at") or "")[:16].replace("T", " ")
    verdicts = {
        0: "D+0 当日执行 (盘后即动, COHR 8/19 范本, 零附加税)",
        1: ("D+1 次交易日执行 — 8/22 裁决要求当日 (盘后可成交即当晚); "
            "若当晚流动性不可行属最快可行, 可改判"),
    }
    verdict = verdicts.get(delay, f"D+{delay} = exit_overdue 形状 "
                                  "(override 三用三付费先例), 规则外 C 起步")
    return (f"auto(机械分, 只评 时延+全平): 破位裁决 {demote.get('date')}"
            + (f" (旧 stop {lvl:g} 被破)" if lvl else "")
            + (f" → 全平 @{px:g}" if px else " → 全平")
            + f" 于 {when} ET, {verdict}"
            + (f"; realized ${pnl:+,.2f}" if pnl is not None else "")
            + "。逻辑/checklist 归人; 防守向不适用 错+好 D 陷阱 (AAOI 8/21 先例); "
              "改判权在 用户 (MRVL 8/22 C→B 先例)")


def plan(doc: dict, scores: Optional[List[dict]] = None,
         bars_of=None) -> dict:
    """→ {"rows": [待落评分行], "skipped": [说明]}。纯函数可测; scores/bars 可注入。"""
    if scores is None:
        scores = list(iter_jsonl(SCORES_PATH))
    rows: List[dict] = []
    skipped: List[str] = []
    for tk, body in sorted(((doc or {}).get("tickers") or {}).items()):
        if str(body.get("status")) != "closed":
            continue
        ei = body.get("exit_info") or {}
        closed_at = str(ei.get("closed_at") or "")
        if not closed_at:
            continue                                    # 收尾未落, 归 close_reconcile
        demote = latest_demotion(body.get("agent"))
        if not demote:
            continue                                    # 止盈清仓 — 评分归人 (§8)
        ts = parse_ts(closed_at)
        closed_date = ts.date().isoformat() if ts else closed_at[:10]
        demote_date = str(demote["date"])[:10]
        if closed_date < EFFECTIVE_FROM:
            continue                                    # 历史不回填 (裁决向前有效)
        if closed_date < demote_date:
            continue                                    # 平仓早于降级 = 非钟响驱动
        if any(str(s.get("ticker") or "").upper() == tk.upper()
               and str(s.get("phase") or "") == "exit"
               and str(s.get("date") or "") >= demote_date for s in scores):
            continue                                    # 已评 (人工或上轮) → 消音
        delay = _delay_days(tk, demote_date, closed_date, bars_of=bars_of)
        if delay is None:
            skipped.append(f"{tk}: kline 缺失, D+N 算不出 — 归人工 "
                           f"(annotate_event --order-id {ei.get('order_id')} --score)")
            continue
        rows.append({
            "date": closed_date,
            "ticker": tk,
            "score": _score_of(delay),
            "phase": "exit",
            "ref": ei.get("order_id") or f"{tk}:{closed_date}",
            "source": SOURCE,
            "notes": _notes(tk, delay, demote, ei),
        })
    return {"rows": rows, "skipped": skipped}


def run(*, dry_run: bool = False) -> dict:
    doc = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
    out = plan(doc)
    if not dry_run:
        for row in out["rows"]:
            append_jsonl(SCORES_PATH, row)
    return {"dry_run": dry_run, "scored": out["rows"], "skipped": out["skipped"]}


def render(res: dict) -> str:
    rows, skipped = res.get("scored") or [], res.get("skipped") or []
    if not rows and not skipped:
        return "exit_score: 无待评分的结构性退出"
    verb = "待评" if res.get("dry_run") else "已评"
    lines = [f"exit_score: {verb} {len(rows)} 笔结构性退出"]
    for r in rows:
        lines.append(f"  {r['ticker']} {r['score']} ({r['date']}, ref {r['ref']}) — 自动落 scores.jsonl")
    lines.extend(f"  ⚠️ {s}" for s in skipped)
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只印计划, 不写盘")
    a = ap.parse_args(argv)
    res = run(dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False) if a.json else render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
