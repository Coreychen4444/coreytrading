"""Time conventions — exactly two, applied everywhere:

1. State metadata (generated_at, as_of, last_reviewed, synced_at):
   UTC ISO 8601 with 'Z' suffix → now_utc_iso()
2. Trade events (events/<YYYY-MM>/trades.jsonl timestamps):
   ISO 8601 with explicit America/New_York offset (trading-day semantics;
   month bucket = ET calendar month) → et_naive_to_iso() / now_et_iso()

moomoo OpenD returns ET wall-clock naive strings (see
.claude/rules/tools-environment.md). NEVER append 'Z' to them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SGT = ZoneInfo("Asia/Singapore")


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def now_et_iso() -> str:
    return datetime.now(ET).isoformat()


def et_today() -> str:
    """今天是哪个**交易日历日**(ET), YYYY-MM-DD。

    凡是要和市场数据比较或并列的日期都必须用它, 不能用 date.today() ——
    后者返回系统本地日期 (SGT), 而 kline 的 time / 期权 expiry / 卡片
    valid_until 全是 ET 日历日。SGT 从 ET 正午起就已经是"明天"了, 于是:

      · exit_guard 的 armed_date 被戳成  (ET 那天是**周六**, 没有
        交易), `since = [c for t,c in closes if t >= armed_date]` 直接筛空,
        高水位只剩 seed_close 撑着 (实测)
      · 同一次巡逻 SGT 早上跑和晚上跑会得到差一天的 armed_date

    与 events 时间戳 (带 ET offset) 和 kline 桶同一口径。
    """
    return datetime.now(ET).date().isoformat()


def et_session_state(now: datetime | None = None) -> str:
    """当前 ET 常规交易时段的状态: "pre" | "open" | "closed" | "weekend"。

    只看钟点和星期, **不查假日表**—— 假日会被报成 pre/open, 靠"那天没有新 bar"
    自然兜底; 半日市 (13:00 收) 会被报成 open 到 16:00, 属保守方向 (晚一小时
    才认收盘, 不会把未完成 bar 当收盘)。
    """
    n = now or datetime.now(ET)
    if n.weekday() >= 5:
        return "weekend"
    m = n.hour * 60 + n.minute
    if m < 9 * 60 + 30:
        return "pre"
    return "open" if m < 16 * 60 else "closed"


def et_session_open(now: datetime | None = None) -> bool:
    """常规时段进行中 —— 此刻"今天"的日线还没定型。

     源案例: ET 13:56 盘中跑 pipeline, kline_cache 把当日进行中的 bar
    (C=166.41 = 那一刻的现价) 落进缓存, regime_transition 拿它当收盘, 判定
    "收盘 166.41 < 卡片 stop 176" 并**永久**把 NBIS 卡片降了一档 + 写进
    regime_history。那根 bar 当天还剩两小时。

    整个收盘依赖层 (regime_transition / exit_guard 棘轮与趋势线 / entry_guard
    gate 计数 / zone staleness) 都把 rows[-1] 读作"昨收"。此前唯一的保护是
    "定时任务在 ET 盘前跑" —— 一个时间巧合, 不是机制。
    """
    return et_session_state(now) == "open"


def et_naive_to_iso(s: str) -> str | None:
    """Parse a moomoo ET wall-clock string (' 15:03:48.552' or ISO-ish)
    into ISO 8601 with explicit ET offset. Returns None if unparseable.
    Strings that already carry a timezone are passed through normalized."""
    if not s:
        return None
    s = str(s).strip().replace(" ", "T")
    if len(s) == 10:  # date-only
        s += "T00:00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.isoformat()


def parse_ts(s: str) -> datetime | None:
    """Robust parse of any timestamp written by this system (Z / ±offset /
    naive-assumed-ET legacy). Returns aware datetime or None."""
    if not s:
        return None
    s = str(s).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt
