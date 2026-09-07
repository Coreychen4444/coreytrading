"""As-of lookup must not expose future zones or another ticker."""
from quant.research import zone_timeline


def test_asof_excludes_future_and_other_tickers():
    rows = [dict(date='2026-09-01', ticker='A', zones={'stop_level': 90}),
            dict(date='2026-09-02', ticker='B', zones={'stop_level': 200}),
            dict(date='2026-09-03', ticker='A', zones={'stop_level': 110})]
    assert zone_timeline.zones_asof(rows, 'A', '2026-08-31') is None
    assert zone_timeline.zones_asof(rows, 'A', '2026-09-02') == {'stop_level': 90}
    assert zone_timeline.zones_asof(rows, 'A', '2026-09-03') == {'stop_level': 110}
