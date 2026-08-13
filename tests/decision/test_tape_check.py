"""tape_check 纯函数回归: session 过滤 / VWAP / 标签阈值 / 空集允许。不打网络。"""
from quant.decision import tape_check as tc


def bar(t, o, h, l, c, v, turn=None):
    return {"time": t, "open": o, "high": h, "low": l, "close": c,
            "volume": v, "turnover": turn if turn is not None else v * (o + c) / 2.0}


def test_session_bars_filters_by_date_prefix():
    bars = [bar("2026-08-04 09:30:00", 1, 2, 1, 2, 10),
            bar("2026-08-05 09:30:00", 1, 2, 1, 2, 10)]
    assert len(tc.session_bars(bars, "2026-08-04")) == 1


def test_empty_and_zero_volume_are_legal_no_reads():
    assert tc.tape_metrics([])["ok"] is False
    assert tc.tape_metrics([bar("d", 1, 2, 1, 2, 0)])["ok"] is False


def test_vwap_from_turnover():
    bars = [bar("d 09:30:00", 100, 101, 99, 100, 10, turn=1000.0),
            bar("d 10:30:00", 110, 111, 109, 110, 10, turn=1200.0)]
    assert tc.tape_metrics(bars)["vwap"] == 110.0


def test_gap_and_day_change_need_prev_close():
    bars = [bar("d 09:30:00", 104, 106, 103, 105, 10)]
    m = tc.tape_metrics(bars, prev_close=100.0)
    assert m["gap_pct"] == 4.0 and m["day_change_pct"] == 5.0
    assert "gap_pct" not in tc.tape_metrics(bars)


def test_label_climax_fade():
    bars = [bar("d 09:30:00", 100, 110, 100, 105, 40)] + [
        bar(f"d 1{i}:30:00", 105 - i, 106 - i, 100.5, 101, 10) for i in range(6)]
    m = tc.tape_metrics(bars)
    assert m["first_share"] >= 0.35 and m["close_pos"] <= 0.40
    assert m["label"] == "climax_fade"


def test_label_drive_hold():
    bars = [bar("d 09:30:00", 100, 108, 100, 107, 40)] + [
        bar(f"d 1{i}:30:00", 107 + i * 0.4, 110, 106.5, 109.5, 10) for i in range(6)]
    m = tc.tape_metrics(bars)
    assert m["label"] == "drive_hold"


def test_label_absorption_back_loaded():
    vols = [10, 10, 10, 20, 25, 25]
    closes = [100, 99, 98.5, 101, 104, 105.8]
    bars = [bar(f"d 1{i}:00:00", closes[i - 1] if i else 100, max(closes[i], 100) + 0.2,
                min(closes[i], 98), closes[i], vols[i]) for i in range(6)]
    m = tc.tape_metrics(bars)
    assert m["back_half_share"] >= 0.55 and m["first_share"] < 0.35
    assert m["label"] == "absorption"


def test_label_steady_trend():
    vols = [16, 16, 18, 16, 17, 17]
    bars = [bar(f"d 1{i}:00:00", 100 + i, 101.4 + i, 99.8 + i, 101 + i, vols[i])
            for i in range(6)]
    m = tc.tape_metrics(bars)
    assert m["max_bar_share"] <= 0.30
    assert m["label"] == "steady_trend"


def test_label_mixed_is_legal_output():
    bars = [bar("d 09:30:00", 100, 110, 100, 105, 40)] + [
        bar(f"d 1{i}:30:00", 105, 106, 104, 105, 10) for i in range(6)]
    m = tc.tape_metrics(bars)                      # 首根重但收盘位中性 → 不硬贴
    assert m["label"] == "mixed"


def test_weak_high_flags_low_volume_afternoon_high():
    bars = [bar("d 09:30:00", 100, 105, 99, 104, 40),
            bar("d 10:30:00", 104, 104.5, 103, 104, 30),
            bar("d 11:30:00", 104, 104.8, 103.5, 104.2, 10),
            bar("d 14:30:00", 104.2, 106, 104, 105.5, 5)]     # 午后新高, 量占 5/85<15%
    assert tc.tape_metrics(bars)["weak_high"] is True
