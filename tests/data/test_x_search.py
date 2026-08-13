"""x_search 纯函数回归: query 组装 / 参数 clamp / 渲染。不打网络。"""
from datetime import datetime, timezone

from quant.data import x_search as xs


# ── build_query ──────────────────────────────────────────────

def test_build_query_appends_retweet_filter():
    assert xs.build_query("NVDA Blackwell") == "NVDA Blackwell -is:retweet"


def test_build_query_raw_passthrough():
    assert xs.build_query("NVDA", raw=True) == "NVDA"


def test_build_query_no_double_append():
    q = "INTC -is:retweet"
    assert xs.build_query(q) == q


# ── build_params ─────────────────────────────────────────────

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


def test_build_params_start_time_from_hours():
    p = xs.build_params("q", hours=24, now=NOW)
    assert p["start_time"] == "2026-08-04T12:00:00Z"


def test_build_params_clamps_cost_and_window():
    p = xs.build_params("q", max_results=100, hours=999, now=NOW)
    assert p["max_results"] == xs.MAX_RESULTS_CAP          # 成本上限
    assert p["start_time"] == "2026-07-29T13:00:00Z"       # 167h = 7天-1h
    lo = xs.build_params("q", max_results=3, now=NOW)
    assert lo["max_results"] == xs.MAX_RESULTS_FLOOR       # API 下限


def test_build_params_requests_note_tweet_and_followers():
    p = xs.build_params("q", now=NOW)
    assert "note_tweet" in p["tweet.fields"]               # 长推全文
    assert p["expansions"] == "author_id"
    assert "public_metrics" in p["user.fields"]            # 粉丝数


# ── render ───────────────────────────────────────────────────

FIXTURE = {
    "data": [{
        "id": "123", "author_id": "u1", "created_at": "2026-08-05T02:31:00.000Z",
        "text": "short text",
        "note_tweet": {"text": "long form body\nwith newline"},
        "public_metrics": {"like_count": 152, "retweet_count": 34},
    }],
    "includes": {"users": [{"id": "u1", "username": "godot",
                            "public_metrics": {"followers_count": 21000}}]},
    "meta": {"result_count": 1},
}


def test_render_prefers_note_tweet_and_flattens_newlines():
    out = xs.render(FIXTURE)
    assert "long form body with newline" in out
    assert "short text" not in out


def test_render_carries_author_metrics_link_and_fyi_discipline():
    out = xs.render(FIXTURE)
    assert "@godot(21,000粉)" in out
    assert "♥152" in out
    assert "https://x.com/i/status/123" in out
    assert "[FYI]" in out                                   # 情报层纪律标注

def test_render_empty_result_is_friendly():
    assert "无匹配结果" in xs.render({"data": [], "meta": {"result_count": 0}})
