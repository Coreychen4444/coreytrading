"""Fake OpenD contexts pin actual quote clocks separately from retrieval clocks."""
import pytest

from quant.data import market_data as md


class Frame:
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows
        self.iloc = self

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, key):
        return self.rows[key]


@pytest.fixture
def provider(monkeypatch):
    def configure(row):
        class Context:
            def subscribe(self, *args, **kwargs):
                return md.RET_OK, None

            def get_stock_quote(self, *args):
                return md.RET_OK, Frame([row])

            def get_market_snapshot(self, *args):
                return md.RET_OK, Frame([row])

            def close(self):
                pass

        monkeypatch.setattr(md, "create_quote_context", Context)
        monkeypatch.setattr(md, "_now_iso", lambda: "2026-09-05T10:00:00+00:00")
    return configure


def test_subscription_uses_provider_data_date_time_not_host_retrieval(provider):
    provider({"code": "US.SYN", "last_price": 100, "data_date": "2026-09-04", "data_time": "15:59:59.123"})
    row = md.get_quote(["SYN"])["SYN"]
    assert row["quote_time"] == "2026-09-04T15:59:59.123000-04:00"
    assert row["quote_time_raw"] == "2026-09-04 15:59:59.123"
    assert row["quote_time_basis"] == "opend_data_date_time"
    assert row["quote_retrieved_at"] == "2026-09-05T10:00:00+00:00"


def test_subscription_missing_vendor_time_stays_unknown(provider):
    provider({"code": "US.SYN", "last_price": 100})
    row = md.get_quote(["SYN"])["SYN"]
    assert row["quote_time"] is None
    assert row["quote_retrieved_at"]


def test_snapshot_retains_raw_vendor_clock_and_normalizes_timezone(provider):
    provider({"code": "US.SYN", "last_price": 100, "update_time": "2026-12-01 16:00:01"})
    row = md.get_snapshot(["SYN"])["SYN"]
    assert row["update_time"] == "2026-12-01T16:00:01-05:00"
    assert row["update_time_raw"] == "2026-12-01 16:00:01"
    assert row["quote_time_basis"] == "opend_snapshot_update_time"


def test_snapshot_missing_vendor_time_does_not_use_host_now(provider):
    provider({"code": "US.SYN", "last_price": 100})
    assert md.get_snapshot(["SYN"])["SYN"]["update_time"] is None
