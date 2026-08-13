#!/usr/bin/env python3
"""x_search.py — X (Twitter) 关键词搜索(官方 API /2/tweets/search/recent),情报层用。

用法:
    python3 quant/data/x_search.py "NVDA Blackwell" [--max 10] [--hours 24] [--sort recency|relevancy] [--raw] [--json]
    python3 quant/data/x_search.py "(INTC OR Intel) 18A" --hours 48
    python3 quant/data/x_search.py "from:GodotSancho" --hours 168      # 白名单 KOL 近一周
    python3 quant/data/x_search.py "optical transceiver ban" --sort relevancy

定位(用户 定):
- **全面替代 bird**(非官方 cookie 通道,随时会断且无告警 = 静默失效源,弃用)。
- 主动搜索用本脚本(像 WebSearch 一样);深读单条 post / X Article 全文仍用 x_fetch.py。
- 凭证与 x_fetch 同一份: .env 的 X_BEARER_TOKEN(App-only Bearer,quant.core.env 加载)。

纪律(v3 情报层):
- 产出只作 [FYI] 情报——零解读、每条带时间与链接;X post 默认 unverified(KOL)/
  partial(转述官方),verified 分级仅标注,不驱动仓位(technical-first directive)。
- **计费**: X API pay-per-use 按读取条数计费——max_results 默认 10、硬上限 25,
  不做无 query 宽拉;search/recent 只覆盖**近 7 天**(hours 上限 167)。

query 常用语法: 空格=AND / "A OR B" / from:user / -is:retweet(默认自动追加,--raw 关闭)
/ lang:en / url:keyword。$CASHTAG 需要更高层级,普通关键词即可。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from quant.core import env  # noqa: F401  (.env → os.environ)

API = "https://api.x.com/2/tweets/search/recent"

MAX_RESULTS_FLOOR = 10   # API 下限
MAX_RESULTS_CAP = 25     # 成本上限(pay-per-use 按条计费)
HOURS_CAP = 167          # search/recent 只覆盖近 7 天,留 1h 余量


def build_query(q: str, raw: bool = False) -> str:
    """默认排除转推(计费按条,转推是重复信息);--raw 时原样透传。"""
    q = q.strip()
    if raw or "is:retweet" in q:
        return q
    return f"{q} -is:retweet"


def build_params(query: str, max_results: int = 10, hours: int = 24,
                 sort: str = "recency", now: datetime | None = None) -> dict:
    """组 API 参数。max/hours 都做硬 clamp,now 可注入(测试用)。"""
    mr = max(MAX_RESULTS_FLOOR, min(MAX_RESULTS_CAP, int(max_results)))
    hrs = max(1, min(HOURS_CAP, int(hours)))
    ref = now or datetime.now(timezone.utc)
    start = (ref - timedelta(hours=hrs)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "query": query,
        "max_results": mr,
        "start_time": start,
        "sort_order": sort,
        "tweet.fields": "created_at,author_id,note_tweet,public_metrics,lang",
        "expansions": "author_id",
        "user.fields": "name,username,verified,public_metrics",
    }


def fetch(params: dict, bearer: str) -> dict:
    req = urllib.request.Request(
        API + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": f"Bearer {bearer}"},
)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            raise SystemExit("[x_search] 401 — bearer token 失效,去 developer portal "
                             "regenerate 后更新 .env 的 X_BEARER_TOKEN")
        if e.code == 429:
            raise SystemExit("[x_search] 429 — rate limit,稍后再试(免费/低层级窗口按 24h 计)")
        if e.code == 400:
            raise SystemExit(f"[x_search] 400 — query 语法错误: {body[:300]}")
        raise SystemExit(f"[x_search] HTTP {e.code}: {body[:300]}")


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def render(data: dict) -> str:
    """每条一行: [时间] @user(粉丝) 互动 | 正文 | 链接。note_tweet(长推全文)优先。"""
    rows = data.get("data") or []
    if not rows:
        errs = data.get("errors", [])
        detail = errs[0].get("detail") if errs else "无匹配结果(检查 query / 时间窗)"
        return f"[x_search] {detail}"
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    out = []
    for tw in rows:
        u = users.get(tw.get("author_id"), {})
        followers = (u.get("public_metrics") or {}).get("followers_count")
        who = f"@{u.get('username', '?')}" + (f"({followers:,}粉)" if followers is not None else "")
        pm = tw.get("public_metrics", {})
        note = tw.get("note_tweet") or {}
        text = _one_line(note.get("text") or tw.get("text", ""))
        ts = (tw.get("created_at") or "?")[:16].replace("T", " ")
        out.append(f"- [{ts}Z] {who} ♥{pm.get('like_count', 0)} rt{pm.get('retweet_count', 0)} "
                   f"| {text} | https://x.com/i/status/{tw['id']}")
    meta = data.get("meta", {})
    out.append(f"({meta.get('result_count', len(rows))} 条 | [FYI] 情报层: 零解读, "
               f"KOL=unverified / 转述官方=partial)")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="X 关键词搜索(官方 API, 情报层 [FYI])")
    ap.add_argument("query", help='X query 语法, 如 "(INTC OR Intel) 18A" 或 "from:user"')
    ap.add_argument("--max", type=int, default=10, help=f"返回条数(计费按条), {MAX_RESULTS_FLOOR}-{MAX_RESULTS_CAP}")
    ap.add_argument("--hours", type=int, default=24, help=f"回看小时数, 上限 {HOURS_CAP}(API 只覆盖 7 天)")
    ap.add_argument("--sort", choices=["recency", "relevancy"], default="recency")
    ap.add_argument("--raw", action="store_true", help="不自动追加 -is:retweet")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    bearer = os.environ.get("X_BEARER_TOKEN")
    if not bearer:
        raise SystemExit("[x_search] 缺 X_BEARER_TOKEN(.env 或环境变量)")
    params = build_params(build_query(args.query, raw=args.raw),
                          max_results=args.max, hours=args.hours, sort=args.sort)
    data = fetch(params, bearer)
    if args.as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(data))


if __name__ == "__main__":
    main()
