from __future__ import annotations

import pandas as pd

from traid_live.forecast import enforce_market_constraints
from traid_live.market import get_timeframe, normalize_symbol
from traid_live.providers.base import CandleProvider, MarketQuote
from traid_live.trading import next_trailing_stop


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


def test_market_quote_serializes_timestamp() -> None:
    quote = MarketQuote(
        symbol="XAUUSD",
        timestamp=pd.Timestamp("2026-07-31T12:00:00Z"),
        price=3300.25,
        bid=3300.2,
        ask=3300.3,
        spread=0.1,
        delayed=False,
        source="test",
    )
    payload = quote.to_dict()
    assert payload["timestamp"] == "2026-07-31T12:00:00+00:00"
    assert payload["price"] == 3300.25


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


def test_buy_trailing_stop_only_tightens() -> None:
    candidate = next_trailing_stop(
        side="buy",
        current_price=110.0,
        open_price=100.0,
        current_sl=104.0,
        distance=3.0,
        step=1.0,
        activation=5.0,
        min_stop_distance=0.5,
        digits=2,
    )
    assert candidate == 107.0

    no_change = next_trailing_stop(
        side="buy",
        current_price=107.5,
        open_price=100.0,
        current_sl=105.0,
        distance=3.0,
        step=1.0,
        activation=5.0,
        min_stop_distance=0.5,
        digits=2,
    )
    assert no_change is None


def test_sell_trailing_stop_only_tightens() -> None:
    candidate = next_trailing_stop(
        side="sell",
        current_price=90.0,
        open_price=100.0,
        current_sl=96.0,
        distance=3.0,
        step=1.0,
        activation=5.0,
        min_stop_distance=0.5,
        digits=2,
    )
    assert candidate == 93.0
