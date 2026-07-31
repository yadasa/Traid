from __future__ import annotations

import pandas as pd

from traid_live.forecast import enforce_market_constraints
from traid_live.market import get_timeframe, normalize_symbol
from traid_live.providers.base import CandleProvider


class FakeProvider(CandleProvider):
    name = "fake"

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        raise NotImplementedError


def test_symbol_aliases_are_normalized() -> None:
    assert normalize_symbol("gold") == "XAUUSD"
    assert normalize_symbol("XAG/USD") == "XAGUSD"
    assert normalize_symbol("ndx") == "NAS100"
    assert normalize_symbol("us500") == "SPX500"


def test_timeframe_metadata() -> None:
    timeframe = get_timeframe("5m")
    assert timeframe.seconds == 300
    assert timeframe.massive_multiplier == 5


def test_future_timestamps_skip_weekend() -> None:
    provider = FakeProvider()
    friday = pd.Timestamp("2026-07-31T23:55:00Z")
    result = provider.future_timestamps("XAUUSD", "5m", friday, 2)
    assert len(result) == 2
    assert all(timestamp.weekday() < 5 for timestamp in result)
    assert result[0] == pd.Timestamp("2026-08-03T00:00:00Z")


def test_decoded_candle_constraints_are_repaired() -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0],
            "high": [98.0],
            "low": [105.0],
            "close": [102.0],
            "volume": [-1.0],
            "amount": [-2.0],
        }
    )
    repaired = enforce_market_constraints(frame)
    assert repaired.loc[0, "high"] == 102.0
    assert repaired.loc[0, "low"] == 100.0
    assert repaired.loc[0, "volume"] == 0.0
    assert repaired.loc[0, "amount"] == 0.0
