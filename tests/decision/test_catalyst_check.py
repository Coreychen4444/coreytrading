"""catalyst_check —— 异动归因的机械校验。

源案例  简报 (用户 指出): 「触发源 = TSMC 周四盘后财报」——
TSMC 财报实为 7/16 (差一周), 7/23 盘后真正的财报是 INTC, 而 NBIS 7/24 那根
-15% 被安上了 7/16 那根 -13.9% 的理由, tape 早在 7/21 就把那套叙事收复并超过跌前。

真实 NBIS 收盘序列 (kline 实测):
    07-15 199.51 | 07-16 171.77 (-13.9%) | 07-17 177.71 | 07-20 182.62
    07-21 216.92 (+18.8%) | 07-22 218.16 | 07-23 220.97 | 07-24 187.77 (-15.0%)
"""

from quant.decision import catalyst_check as cc  # noqa: E402

NBIS = [("2026-07-15", 199.51), ("2026-07-16", 171.77), ("2026-07-17", 177.71),
        ("2026-07-20", 182.62), ("2026-07-21", 216.92), ("2026-07-22", 218.16),
        ("2026-07-23", 220.97), ("2026-07-24", 187.77)]
PEERS = {"INTC": [("2026-07-23", 100.23), ("2026-07-24", 92.32)],
         "MU": [("2026-07-23", 990.21), ("2026-07-24", 920.95)],
         "LITE": [("2026-07-23", 833.64), ("2026-07-24", 762.99)],
         "MRVL": [("2026-07-23", 209.32), ("2026-07-24", 194.23)]}


def _patch(monkeypatch):
    monkeypatch.setattr(cc, "closes", lambda t: NBIS if t == "NBIS" else PEERS.get(t, []))
    monkeypatch.setattr(cc, "SECTOR_PEERS", list(PEERS))


def _cat(date, scope="sector", url="https://example.com/x", head="H"):
    return {"date": date, "headline": head, "url": url, "scope": scope}


# ── 移动幅度用 kline 算, 不接受口报 ────────────────────────────────

def test_move_is_computed_from_klines(monkeypatch):
    _patch(monkeypatch)
    m = cc.move_on("NBIS", "2026-07-24")
    assert m["change_pct"] == -15.02 and m["prev_date"] == "2026-07-23"
    # 7/27 简报里的 "NBIS -10.8%" 是盘前快照比错基准 —— 这里只认收盘对收盘
    assert m["prev_close"] == 220.97


# ── tape 否定 ─────────────────────────────────────────────────────

def test_stale_catalyst_refuted_by_recovery(monkeypatch):
    """7/16 的理由被 7/21-7/23 的收复否掉 —— 不能解释 7/24。"""
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-16")])
    assert not r["triggers"] and len(r["refuted"]) == 1
    ref = r["refuted"][0]["refutation"]
    assert ref["peak"] == 220.97 and ref["pre_close"] == 199.51
    assert "REFUTED_BY_TAPE" in r["refuted"][0]["reason"]


def test_amc_catalyst_one_session_before_is_a_valid_trigger(monkeypatch):
    """INTC 7/23 盘后财报 → 7/24 异动。首版把 catalyst 当日也算进收复窗口,
    于是它被自己那天的收盘"收复"掉 —— 而消息在收盘后才出。"""
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-23")])
    assert len(r["triggers"]) == 1 and not r["refuted"]
    assert r["triggers"][0]["lag_sessions"] == 1


def test_no_refutation_when_price_never_recovered(monkeypatch):
    """一路下跌未收复 → 旧 catalyst 仍算背景, 但不是"被否定"。"""
    _patch(monkeypatch)
    monkeypatch.setattr(cc, "closes", lambda t: (
        [("2026-07-15", 199.51), ("2026-07-16", 171.77), ("2026-07-17", 165.0),
         ("2026-07-20", 160.0), ("2026-07-21", 150.0)] if t == "NBIS" else []))
    r = cc.check("NBIS", "2026-07-21", [_cat("2026-07-16")])
    assert not r["refuted"] and len(r["background"]) == 1


# ── 日期绑定 ──────────────────────────────────────────────────────

def test_old_catalyst_demoted_to_background(monkeypatch):
    """未被 tape 否定但太早 → 背景, 从触发源里剔除。"""
    _patch(monkeypatch)
    monkeypatch.setattr(cc, "closes", lambda t: (
        [("2026-07-20", 182.62), ("2026-07-21", 180.0), ("2026-07-22", 179.0),
         ("2026-07-23", 178.0), ("2026-07-24", 150.0)] if t == "NBIS" else []))
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-20")])
    assert not r["triggers"] and len(r["background"]) == 1
    assert "降级为**背景**" in r["background"][0]["reason"]


def test_same_day_catalyst_is_a_trigger(monkeypatch):
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-24")])
    assert len(r["triggers"]) == 1 and r["triggers"][0]["lag_sessions"] == 0


# ── 合规: 无来源 / 无 scope 不算归因 ──────────────────────────────

def test_catalyst_without_url_is_invalid(monkeypatch):
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-24", url=None)])
    assert not r["triggers"] and "无来源 URL" in r["invalid"][0]["reason"]


def test_catalyst_without_scope_is_invalid(monkeypatch):
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-24", scope=None)])
    assert not r["triggers"] and "scope" in r["invalid"][0]["reason"]


# ── 板块残差 ──────────────────────────────────────────────────────

def test_sector_residual_flags_2x_amplification(monkeypatch):
    """NBIS -15.0% vs 板块中位 —— 超出部分板块解释不了。
    (本 fixture 只有 4 个 peer, 中位 -7.55% → 1.99×; 真实 8 peer 跑出 -7.10% → 2.12×。
    断言对着阈值不对着某个具体数, 免得换 fixture 就红。)"""
    _patch(monkeypatch)
    res = cc.sector_residual("NBIS", "2026-07-24")
    assert res["unexplained"] is True
    assert res["ratio"] > cc.PARAMS["residual_mult"] and res["n_peers"] == 4


def test_sector_move_alone_is_not_unexplained(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(cc, "closes", lambda t: (
        [("2026-07-23", 100.0), ("2026-07-24", 93.0)] if t == "NBIS"
        else PEERS.get(t, [])))
    res = cc.sector_residual("NBIS", "2026-07-24")
    assert res["unexplained"] is False


# ── 空集是合法输出 ────────────────────────────────────────────────

def test_unknown_trigger_is_a_legitimate_output(monkeypatch):
    """散文永远能编出一个理由 —— 这才是最危险的部分。"""
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [])
    txt = cc.render(r)
    assert not r["triggers"]
    assert "**触发源未知**" in txt
    assert "不要用卡片 key_risk 里能对上的条目补这个空" in txt


def test_sector_trigger_does_not_absorb_stock_level_residual(monkeypatch):
    """有触发源 ≠ 解释完了。板块级 catalyst 只解释板块那部分。"""
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-23", scope="sector")])
    txt = cc.render(r)
    assert "✅ 触发源" in txt
    assert "仍无个股级解释" in txt, "板块级触发源被当成了完整解释"


def test_stock_level_trigger_suppresses_the_residual_warning(monkeypatch):
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-24", [_cat("2026-07-23", scope="stock")])
    assert "仍无个股级解释" not in cc.render(r)


# ── 解析 ──────────────────────────────────────────────────────────

def test_parse_catalyst_handles_url_colons():
    c = cc.parse_catalyst("2026-07-23:INTC beat 但回吐:https://a.com/x?y=1:sector")
    assert c["date"] == "2026-07-23" and c["scope"] == "sector"
    assert c["url"] == "https://a.com/x?y=1"
    assert c["headline"] == "INTC beat 但回吐"


def test_intraday_or_missing_bar_errors_out(monkeypatch):
    """kline 只存已完成收盘 —— 盘中查当日会明确报错, 不是编一个数。"""
    _patch(monkeypatch)
    r = cc.check("NBIS", "2026-07-28", [])
    assert "无 2026-07-28 的已完成日线" in r["error"]
