"""next_regime 的 gate 锚由构造给定, 不接受手写 ()。

`--repair-gate-anchor-basis` 那条通道 (用户 裁决) 只保护了
`last_regime.repair_gate` —— **转移史**。真正驱动转移的 `next_regime.entry_gate`
与 `next_gate_preview` 走 `--next-regime` 一整块 raw JSON 原样落盘, 里面的
`anchored_on.date` 写什么就是什么。**唯一没闸的地方恰好是唯一会动钱的地方。**

源案例  (用户  从盘前计划里抓出): 收据复核只刷新了拒绝
计数 (MU 924 拒 ×11→×10, 一根滚出 90 日窗), basis 原文至今写着「60m 真分布复核
****」「故 level 不动」—— 证据没换、level 没动, 锚却盖成 8/11。
后果不只是 entry_guard 判 STALE (MU 那天整天没有入场计划), 更是让一条证据停在
8/06 的 gate **自称更新鲜**。**复核 ≠ 重锚。**
同笔的 MRVL 是镜像: zones 到了 8/11 而 entry_gate 留在 8/06 —— 有闸的
repair_gate 自动跟上了, 没闸的 entry_gate 没有。两个方向, 同一个洞。

⛔ 本通道**永不拦截**(与 8/06 裁决同拍): 硬拦会误伤"只重画 zones 没复核 gate"
(STALE 是诚实信号) 与"转移机骨架无 anchored_on"(归 pending_repaint 管) 两条
合法流程。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quant.core import io_json, paths

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


@pytest.fixture
def upz(monkeypatch):
    spec = importlib.util.spec_from_file_location("upz_next_gate_anchor_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    return mod


def _agent(gate_date="2026-08-11", zone_date="2026-08-06", preview_date=None):
    gate = {"level": 945.0, "days": 2, "volume_ratio_min": 1.2}
    if gate_date:
        gate["anchored_on"] = {"date": gate_date, "basis": "60m 真分布复核 2026-08-06"}
    nr = {"name": "C_trend_repair_945", "entry_gate": gate, "cap_pct_nav": 0.40}
    if preview_date:
        nr["next_gate_preview"] = {"level": 1008.0, "days": 2,
                                   "anchored_on": {"date": preview_date, "basis": "p"}}
    return {"zones": {"accumulation_zone_1": [848.0, 861.0],
                      "anchored_on": {"date": zone_date, "basis": "b"}},
            "next_regime": nr}


class _Args:
    def __init__(self, next_regime=None, anchored_on="", anchor_basis=""):
        self.next_regime = next_regime
        self.anchored_on = anchored_on
        self.anchor_basis = anchor_basis


# ── declare 了 gate → 锚由构造给定 ────────────────────────────────

def test_hand_written_gate_date_is_overwritten_with_the_zone_date(upz, capsys):
    """MU 8/11 的形状: 传 --next-regime 时手写的 8/11 落不了盘。"""
    agent = _agent()
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))

    g = agent["next_regime"]["entry_gate"]
    assert g["anchored_on"]["date"] == "2026-08-06"          # 跟 zones, 不跟手写
    assert "60m 真分布复核" in g["anchored_on"]["basis"]      # basis 原文保留
    assert g["level"] == 945.0                                # level 不动
    out = capsys.readouterr().out
    assert "2026-08-11 → 2026-08-06" in out and "复核 ≠ 重锚" in out


def test_preview_gate_is_synced_too(upz):
    """次档 preview 同样是 gate, 同样不许自带第二个日期。"""
    agent = _agent(gate_date="2026-08-06", preview_date="2026-08-11")
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))
    assert agent["next_regime"]["next_gate_preview"]["anchored_on"]["date"] == "2026-08-06"


def test_already_in_sync_is_untouched_and_silent(upz, capsys):
    agent = _agent(gate_date="2026-08-06")
    before = json.dumps(agent, sort_keys=True)
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))
    assert json.dumps(agent, sort_keys=True) == before
    assert capsys.readouterr().out == ""


# ── 没 declare → 只提醒, 绝不改写 ─────────────────────────────────

def test_zones_only_repaint_warns_but_never_backdates_the_gate_forward(upz, capsys):
    """🚨 本通道最容易写反的一条。

    只重画 zones (没传 --next-regime) 时把旧 gate 的锚拖到今天 = 一条没人复核过的
    线凭空变新鲜 —— 那正是本函数要防的事**反着做一遍**。所以这里只提醒。
    """
    agent = _agent(gate_date="2026-08-06", zone_date="2026-08-12")
    upz.apply_next_gate_anchor(agent, _Args(anchored_on="2026-08-12"))

    assert agent["next_regime"]["entry_gate"]["anchored_on"]["date"] == "2026-08-06"
    out = capsys.readouterr().out
    assert "未随本次重画" in out and "STALE 是诚实信号" in out


def test_untouched_call_says_nothing(upz, capsys):
    """既没 declare gate 也没重锚 zones (改个 --vehicle 之类) → 完全闭嘴。"""
    agent = _agent()
    upz.apply_next_gate_anchor(agent, _Args())
    assert agent["next_regime"]["entry_gate"]["anchored_on"]["date"] == "2026-08-11"
    assert capsys.readouterr().out == ""


# ── 不拦截 / 不误伤 ───────────────────────────────────────────────

def test_machine_written_skeleton_has_no_anchor_and_is_left_alone(upz, capsys):
    """转移机 (apply_transition) 写的 next_regime 骨架只有 level/days/unlocks/
    volume_ratio_min, **没有 anchored_on**—— 它靠 pending_repaint 管, 不归这里。
    在这上面强行盖锚 = 凭空给一条待补画的线发一张新鲜证明。"""
    agent = _agent(gate_date=None, zone_date="2026-08-12")
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))
    assert "anchored_on" not in agent["next_regime"]["entry_gate"]
    assert capsys.readouterr().out == ""


def test_card_without_next_regime_is_a_noop(upz):
    agent = {"zones": {"anchored_on": {"date": "2026-08-12"}}}
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))   # 不抛
    assert "next_regime" not in agent


def test_gate_with_anchor_but_zones_without_one_only_warns(upz, capsys):
    """ 之前画的卡 zones 没有 anchored_on —— 无从同步, 但要说出口
    (混进硬判定实测会让全部卡当场作废)。"""
    agent = _agent(gate_date="2026-08-11")
    agent["zones"].pop("anchored_on")
    upz.apply_next_gate_anchor(agent, _Args(next_regime="{}"))
    assert agent["next_regime"]["entry_gate"]["anchored_on"]["date"] == "2026-08-11"
    assert "zones 无锚" in capsys.readouterr().out


def test_it_never_raises(upz):
    """重画路径上任何一条都不许因为本通道失败 —— 拦截会误伤合法流程。"""
    for agent in ({}, {"next_regime": None}, {"next_regime": {}},
                  {"next_regime": {"entry_gate": None}},
                  {"next_regime": {"entry_gate": {"days": 2}}},          # 无 level
                  {"next_regime": {"entry_gate": {"level": 1, "anchored_on": "x"}}}):
        upz.apply_next_gate_anchor(agent, _Args(next_regime="{}", anchored_on="2026-08-12"))


# ── 端到端: 函数对了 ≠ 接上了 ─────────────────────────────────────
#
# 变异测试实证: 把 `apply_next_gate_anchor(agent, args)` 从写卡路径里删掉,
# 上面那 9 条单测**全绿**—— 它们测的是函数, 不是产品。这正是本仓反复栽的
# 那个形状 (#17「一个不会触发的具体检查, 比没有这条检查更危险」)。
# 下面两条走真 CLI, 落盘再读回来。

@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    spec = importlib.util.spec_from_file_location("upz_next_gate_e2e", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)

    card = {"instrument": {"type": "stock"}, "broker": None, "status": "watching",
            "agent": {"thesis": {"summary": "t", "status": "intact",
                                 "conviction": "medium",
                                 "last_reviewed": "2026-08-06T00:00:00Z"},
                      "zones": {"accumulation_zone_1": [848.0, 861.0],
                                "stop_level": 807.5, "valid_until": "2026-08-20",
                                "anchored_on": {"date": "2026-08-06", "basis": "b"}},
                      "action": {"intent": "wait_for_zone", "reason": "r"},
                      "current_regime": {"name": "B_repair_probe_848",
                                         "cap_pct_nav": 0.02,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-05", "source": "card"}}}
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"MU": card}}), encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")

    def run(argv):
        sys.argv = ["upz", "--ticker", "MU", "--bucket", "watchlist",
                    "--conviction", "medium"] + argv
        mod.main()
        return json.loads(wl.read_text())["tickers"]["MU"]["agent"]
    return run


@pytest.fixture
def cli_pos(monkeypatch, tmp_path):
    """positions 桶 → merge_position() 那条写卡路径。"""
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    spec = importlib.util.spec_from_file_location("upz_next_gate_e2e_pos", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)

    card = {"instrument": {"type": "stock", "role": "core_theme"},
            "broker": {"qty": 2.0, "current_price": 868.52}, "status": "active",
            "agent": {"thesis": {"summary": "t", "status": "intact",
                                 "conviction": "medium",
                                 "last_reviewed": "2026-08-06T00:00:00Z"},
                      "zones": {"accumulation_zone_1": [848.0, 861.0],
                                "stop_level": 807.5, "valid_until": "2026-08-20",
                                "anchored_on": {"date": "2026-08-06", "basis": "b"}},
                      "action": {"intent": "hold", "reason": "r"},
                      "current_regime": {"name": "B_repair_probe_848",
                                         "cap_pct_nav": 0.02,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-05", "source": "card"}},
            "lots": {"base": [], "position": []},
            "position_summary": {"total_qty": 2, "total_cost": 1737.04},
            "pending_orders": []}
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {"MU": card}}), encoding="utf-8")
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")

    def run(argv):
        sys.argv = ["upz", "--ticker", "MU", "--bucket", "current",
                    "--conviction", "medium"] + argv
        mod.main()
        return json.loads(pos.read_text())["tickers"]["MU"]["agent"]
    return run


NR_8_11 = json.dumps({"name": "C_trend_repair_945", "cap_pct_nav": 0.40,
                      "entry_gate": {"level": 945, "days": 2,
                                     "anchored_on": {"date": "2026-08-11",
                                                     "basis": "60m 复核 2026-08-06"}}})


def test_cli_refuses_to_persist_a_hand_written_gate_date(cli):
    """884411d 那笔的形状: 重画 zones 到 8/12 + 手写 gate 锚 8/11 → 落盘必须是 8/12。"""
    agent = cli(["--zone1", "848", "861", "--stop-level", "807.5",
                 "--anchored-on", "2026-08-12", "--anchor-basis", "b2",
                 "--next-regime", NR_8_11])
    g = agent["next_regime"]["entry_gate"]
    assert g["anchored_on"]["date"] == "2026-08-12"       # 手写的 8/11 落不了盘
    assert "60m 复核" in g["anchored_on"]["basis"]         # basis 原文保留
    assert g["level"] == 945                              # level 不动


def test_cli_covers_the_positions_path_too(cli_pos):
    """两条写卡路径 (merge_position / merge_candidate) 各接一次 —— 只测 watchlist
    那条时, 删掉 positions 那个调用点**测试全绿**(变异实证)。每条路径各一个用例。"""
    agent = cli_pos(["--zone1", "848", "861", "--stop-level", "807.5",
                     "--anchored-on", "2026-08-12", "--anchor-basis", "b2",
                     "--next-regime", NR_8_11])
    assert agent["next_regime"]["entry_gate"]["anchored_on"]["date"] == "2026-08-12"


def test_cli_zones_only_repaint_leaves_the_gate_anchor_alone(cli, capsys):
    """反方向同样重要: 没 declare gate 就不许把它的锚拖到今天 (那是给未复核的线
    发新鲜证明)。落盘后 gate 仍是旧锚, entry_guard 照常判 STALE。"""
    cli(["--next-regime", NR_8_11])                       # 先把 8/11 那份种进去
    capsys.readouterr()
    agent = cli(["--zone1", "850", "865", "--stop-level", "810",
                 "--anchored-on", "2026-08-12", "--anchor-basis", "b3"])
    # 第一次调用已把锚同步成 zones 的 8/06; 本次重画到 8/12 但没 declare → 不动
    assert agent["next_regime"]["entry_gate"]["anchored_on"]["date"] == "2026-08-06"
    assert "未随本次重画" in capsys.readouterr().out
