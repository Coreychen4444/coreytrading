"""情报层动态投资范围 CLI —— 外部 agent 只抄它的输出.

判别式本体在 core/holdings.intel_universe (那边的测试管语义); 这里守的是壳:
读对文件、line 的口径与既有报告同款、--json 完整、墓碑永远不进 line。
"""
import json

from quant.core.io_json import atomic_write_json
from quant.ops import ainews_universe as au


POS = {"tickers": {
    "KEEP": {"status": "active", "instrument": {"role": "commemorative_hold"},
             "broker": {"qty": 1.0, "as_of": "2026-01-05T04:00:00Z"}},
    "BBB": {"status": "active", "broker": {"qty": 20.0, "as_of": "2026-01-05T04:10:00Z"}},
    "AAA": {"status": "active", "broker": {"qty": 0.0, "synthetic": True,
                                           "as_of": "2026-01-05T05:00:00Z"}},   # qty 0: 不算
    "AAA270115C001000": {"status": "active",
                         "broker": {"qty": 1.0, "as_of": "2026-01-05T04:05:00Z"}},
    "FLAT": {"status": "active", "broker": {"qty": 0.0}},
    "ARCH": {"status": "closed", "broker": {"qty": 0.0}},
}}
WL = {"tickers": {
    "GONE": {"status": "watching", "moomoo_listed": False},
    "CAND": {"status": "watching", "moomoo_listed": True},
    "FLAT": {"status": "watching", "moomoo_listed": True},
    "AAA": {"status": "watching", "moomoo_listed": True},
    "OLD": {"status": "passed", "moomoo_listed": True},
}}


def _files(tmp_path):
    pos, wl = tmp_path / "positions.json", tmp_path / "watchlist.json"
    atomic_write_json(pos, POS)
    atomic_write_json(wl, WL)
    return pos, wl


def test_build_reads_both_files_and_renders_the_report_line(tmp_path):
    out = au.build(*_files(tmp_path))
    assert out["active"] == ["AAA", "BBB"]
    assert out["watchlist"] == ["CAND", "FLAT"]
    assert out["line"] == "当前 active：AAA、BBB（期权映射回正股并去重）；watchlist：CAND、FLAT"
    assert out["as_of"] == "2026-01-05T04:10:00Z"          # 只看在持行 (qty≠0)
    assert "GONE" in out["excluded"]["watchlist"]
    assert "KEEP" in out["excluded"]["positions"]


def test_tombstones_never_reach_the_line(tmp_path):
    out = au.build(*_files(tmp_path))
    for gone in ("GONE", "OLD", "KEEP", "ARCH"):
        assert gone not in out["line"], gone


def test_cli_json_and_plain_modes(tmp_path, capsys):
    pos, wl = _files(tmp_path)
    assert au.main(["--positions", str(pos), "--watchlist", str(wl), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] == ["AAA", "BBB"] and payload["watchlist"] == ["CAND", "FLAT"]
    assert set(payload) >= {"active", "watchlist", "excluded", "line", "as_of", "source"}

    assert au.main(["--positions", str(pos), "--watchlist", str(wl)]) == 0
    assert capsys.readouterr().out.strip() == payload["line"]


def test_empty_universe_still_renders(tmp_path):
    pos, wl = tmp_path / "p.json", tmp_path / "w.json"
    atomic_write_json(pos, {"tickers": {}})
    atomic_write_json(wl, {"tickers": {}})
    out = au.build(pos, wl)
    assert out["line"] == "当前 active：无（期权映射回正股并去重）；watchlist：无"
    assert out["as_of"] is None
