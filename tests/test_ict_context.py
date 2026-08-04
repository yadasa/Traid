from __future__ import annotations

import pandas as pd

from traid_live.ict_context import analyze_ict


def _candles(count: int = 120) -> pd.DataFrame:
    timestamps = pd.date_range("2026-08-03T00:00:00Z", periods=count, freq="5min")
    closes = [100 + index * 0.05 + ((index % 9) - 4) * 0.03 for index in range(count)]
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": [max(open_, close) + 0.12 for open_, close in zip(opens, closes)],
            "low": [min(open_, close) - 0.12 for open_, close in zip(opens, closes)],
            "close": closes,
            "volume": 1.0,
            "amount": 1.0,
        }
    )


def test_context_exposes_structure_liquidity_and_model() -> None:
    context = analyze_ict(_candles(), symbol="SPX500", timeframe="5m")
    assert context["available"] is True
    assert context["version"] == "ict_smc_v1"
    assert context["structure"]["bias"] in {"bullish", "bearish", "sideways"}
    assert "liquidity" in context
    assert context["context_model"]["dominant"] in {"bullish", "bearish", "no_trade"}
    assert context["context_signature"].startswith("SPX500|5m|")


def test_sell_side_sweep_is_processed_without_breaking_context() -> None:
    frame = _candles()
    reference_low = float(frame.iloc[-8:-1]["low"].min())
    last = frame.index[-1]
    frame.loc[last, "open"] = reference_low + 0.08
    frame.loc[last, "low"] = reference_low - 0.30
    frame.loc[last, "high"] = reference_low + 0.42
    frame.loc[last, "close"] = reference_low + 0.28

    context = analyze_ict(frame, symbol="EURUSD", timeframe="5m")
    sweep = context["liquidity"].get("sweep")
    if sweep:
        assert sweep["direction"] in {"bullish", "bearish"}
    assert context["setup"]["state"] in {"waiting", "developing", "ready", "blocked_event"}


def test_high_impact_event_blocks_setup() -> None:
    frame = _candles()
    timestamp = pd.Timestamp(frame.iloc[-1]["timestamp"])
    event = {
        "title": "USD test event",
        "currency": "USD",
        "impact": "high",
        "starts_at": (timestamp + pd.Timedelta(minutes=10)).isoformat(),
    }
    context = analyze_ict(
        frame,
        symbol="XAUUSD",
        timeframe="5m",
        events=[event],
    )
    assert context["event_risk"]["blocked"] is True
    assert context["setup"]["state"] == "blocked_event"
