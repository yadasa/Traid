from __future__ import annotations

from typing import Any

import pandas as pd

from .forecast import ForecastParameters
from .platform import ForecastPlatform
from .service_patch import (
    ALIGNED_TIMEFRAMES,
    NORMAL_SAMPLE_COUNT,
    aligned_consensus,
    app,
)


_BASE_CONSENSUS = aligned_consensus


def _same_candle(first: Any, second: Any) -> bool:
    try:
        return pd.Timestamp(first) == pd.Timestamp(second)
    except Exception:
        return False


def fresh_aligned_consensus(
    self: ForecastPlatform,
    symbol: str,
    selected_timeframe: str,
) -> dict[str, Any]:
    """Keep 5m, 15m, and 1h forecasts fresh before comparing them."""

    for timeframe in ALIGNED_TIMEFRAMES:
        try:
            candles = self.engine.candles(symbol, timeframe, 2)
            latest_completed = candles["timestamp"].iloc[-1]
            forecasts = self.store.forecasts(symbol, timeframe, 1)
            current = forecasts[0] if forecasts else None
            if current and _same_candle(current.get("input_last_timestamp"), latest_completed):
                continue

            params = ForecastParameters(
                symbol=symbol,
                timeframe=timeframe,
                lookback=self.engine.settings.default_lookback,
                pred_len=max(self.engine.settings.default_pred_len, 12),
                sample_count=NORMAL_SAMPLE_COUNT,
            )
            self.generate(params, advanced=False)
        except Exception:
            # Consensus will mark this timeframe unknown instead of breaking the
            # selected market forecast when one auxiliary feed is unavailable.
            continue

    return _BASE_CONSENSUS(self, symbol, selected_timeframe)


ForecastPlatform.consensus = fresh_aligned_consensus  # type: ignore[method-assign]
