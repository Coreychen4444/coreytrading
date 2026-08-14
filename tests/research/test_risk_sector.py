"""risk_sector 机械件验证：分离度与聚类稳定性必须同时受检。"""
import pytest

from quant.research import risk_sector as rs


def test_ari_identical_partitions_is_one():
    a = {"x": 0, "y": 0, "z": 1, "w": 1}
    assert rs.ari(a, dict(a)) == pytest.approx(1.0)


def test_ari_relabelled_partition_is_still_one():
    """ARI 必须对标签重命名不变 —— 组 0/1 换成 1/0 是同一个分组。"""
    a = {"x": 0, "y": 0, "z": 1, "w": 1}
    b = {"x": 7, "y": 7, "z": 3, "w": 3}
    assert rs.ari(a, b) == pytest.approx(1.0)


def test_ari_near_zero_for_unrelated_partitions():
    a = {c: i // 4 for i, c in enumerate("abcdefgh")}
    b = {c: i % 2 for i, c in enumerate("abcdefgh")}
    assert abs(rs.ari(a, b)) < 0.3


def test_separation_is_within_minus_between():
    C = {("a", "b"): 0.9, ("b", "a"): 0.9,
         ("c", "d"): 0.8, ("d", "c"): 0.8,
         ("a", "c"): 0.1, ("c", "a"): 0.1,
         ("a", "d"): 0.1, ("d", "a"): 0.1,
         ("b", "c"): 0.2, ("c", "b"): 0.2,
         ("b", "d"): 0.2, ("d", "b"): 0.2}
    labels = {"a": 0, "b": 0, "c": 1, "d": 1}
    sep, nw, nb = rs.separation(C, labels)
    assert nw == 2 and nb == 4
    assert sep == pytest.approx((0.9 + 0.8) / 2 - 0.15)


def test_hierarchical_puts_correlated_pairs_together():
    C = {("a", "b"): 0.95, ("b", "a"): 0.95,
         ("c", "d"): 0.95, ("d", "c"): 0.95}
    for x in "ab":
        for y in "cd":
            C[(x, y)] = C[(y, x)] = 0.05
    labels, clusters = rs.hierarchical(C, ["a", "b", "c", "d"], 2)
    assert labels["a"] == labels["b"] and labels["c"] == labels["d"]
    assert labels["a"] != labels["c"]
    assert sorted(len(c) for c in clusters) == [2, 2]


def test_drop_isolated_removes_tickers_that_cannot_be_paired():
    C = {("a", "b"): 0.5, ("b", "a"): 0.5, ("a", "c"): 0.5, ("c", "a"): 0.5,
         ("b", "c"): 0.5, ("c", "b"): 0.5}
    keep, dropped = rs.drop_isolated(C, ["a", "b", "c", "lonely"])
    assert "lonely" in dropped and set(keep) == {"a", "b", "c"}


def test_preregistered_constants_are_pinned():
    assert rs.SHOCK_MEDIAN == -1.0
    assert rs.K_VALUES == (2, 3, 4, 5, 6, 8, 10)
    assert (rs.SEP_MIN, rs.MAX_GROUP_FRAC, rs.ARI_MIN) == (0.15, 0.50, 0.30)
    assert rs.MIN_COMMON_DAYS == 15


def test_stability_gate_remains_part_of_the_contract():
    """只测 separation 会漏掉不稳定聚类，因此 ARI 闸门必须保留。"""
    assert rs.ARI_MIN == 0.30
