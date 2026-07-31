from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import pandas as pd

from ..market import get_timeframe, normalize_symbol


CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "amount"]


class MarketDataError(RuntimeError):
    """Raised when a market-data provider cannot return valid candles."""


class CandleProvider(ABC):
    name: str

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Return completed candles in chronological order."""

    def future_timestamps(
        self,
        symbol: str,
        timeframe: str,
        last_timestamp: pd.Timestamp,
        periods: int,
    ) -> pd.DatetimeIndex:
        """Build a 24/5 schedule suitable for broker CFDs and spot metals."""
        normalize_symbol(symbol)
        tf = get_timeframe(timeframe)
        current = pd.Timestamp(last_timestamp)
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        else:
            current = current.tz_convert("UTC")

        output: list[pd.Timestamp] = []
        while len(output) < periods:
            current += pd.Timedelta(seconds=tf.seconds)
            # Most broker CFD feeds pause over the weekend. Exact holidays/session
            # breaks remain provider/broker-specific and are learned from returned bars.
            if current.weekday() >= 5:
                continue
            output.append(current)
        return pd.DatetimeIndex(output)

    @staticmethod
    def normalize_frame(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
        missing = set(CANDLE_COLUMNS) - set(frame.columns)
        if missing:
            raise MarketDataError(f"Provider response is missing columns: {sorted(missing)}")
        if frame.empty:
            raise MarketDataError("Provider returned no candles.")

        clean = frame[CANDLE_COLUMNS].copy()
        clean["timestamp"] = pd.to_datetime(clean["timestamp"], utc=True)
        numeric = ["open", "high", "low", "close", "volume", "amount"]
        clean[numeric] = clean[numeric].apply(pd.to_numeric, errors="coerce")
        clean = clean.dropna(subset=["timestamp", "open", "high", "low", "close"])
        clean["volume"] = clean["volume"].fillna(0.0).clip(lower=0.0)
        clean["amount"] = clean["amount"].fillna(0.0).clip(lower=0.0)
        clean = clean.drop_duplicates(subset=["timestamp"], keep="last")
        clean = clean.sort_values("timestamp").tail(limit).reset_index(drop=True)

        if len(clean) < 2:
            raise MarketDataError("At least two completed candles are required.")
        return clean

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
