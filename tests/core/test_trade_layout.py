"""Production paths must agree after removing the market wrapper."""
from quant.core import paths, io_json
from quant.sync import state_io


def test_shared_roots_and_lock():
    assert paths.POSITIONS_PATH == paths.ROOT / 'trade/portfolio/state/positions.json'
    assert state_io.POSITIONS_PATH == paths.POSITIONS_PATH
    assert io_json.STATE_LOCK_PATH == paths.ROOT / 'var/portfolio/.state.lock'
    assert paths.CATALYSTS_PATH == paths.ROOT / 'trade/portfolio/observations/catalysts.jsonl'
    assert paths.STRUCTURE_DRIFT_PATH == paths.ROOT / 'trade/portfolio/observations/structure_drift.jsonl'
    assert paths.INTELLIGENCE_DIR == paths.ROOT / 'trade/intelligence'
    assert paths.MARKET_DIR == paths.ROOT / 'trade/market'
