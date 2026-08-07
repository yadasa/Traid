from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from model import Kronos, KronosPredictor, KronosTokenizer

from .config import Settings
from .market import normalize_symbol
from .providers import CandleProvider, build_provider


@dataclass(frozen=True)
class ForecastParameters:
    symbol: str
    timeframe: str = "5m"
    lookback: int = 400
    pred_len: int = 24
    temperature: float = 1.0
    top_k: int = 0
    top_p: float = 0.9
    sample_count: int = 5


class ForecastEngine:
    """Coordinates completed market candles with the pretrained Kronos model."""

    def __init__(
        self,
        settings: Settings | None = None,
        provider: CandleProvider | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.provider = provider or build_provider(self.settings)
        self._predictor: KronosPredictor | None = None
        self._model_lock = threading.RLock()

    @property
    def predictor(self) -> KronosPredictor:
        # Hugging Face weights are intentionally loaded lazily so health checks and
        # candle-only endpoints start immediately.
        if self._predictor is None:
            with self._model_lock:
                if self._predictor is None:
                    tokenizer = KronosTokenizer.from_pretrained(
                        self.settings.tokenizer_id
                    )
                    model = Kronos.from_pretrained(self.settings.model_id)
                    self._predictor = KronosPredictor(
                        model=model,
                        tokenizer=tokenizer,
                        device=self.settings.model_device,
                        max_context=self.settings.max_context,
                    )
        return self._predictor

    def candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        canonical = normalize_symbol(symbol)
        if limit < 2:
            raise ValueError("limit must be at least 2.")
        return self.provider.get_candles(canonical, timeframe, limit)

    @staticmethod
    def _validate_params(params: ForecastParameters) -> None:
        if params.lookback < 2:
            raise ValueError("lookback must be at least 2.")
        if params.pred_len < 1:
            raise ValueError("pred_len must be at least 1.")
        if params.sample_count < 1:
            raise ValueError("sample_count must be at least 1.")
        if params.temperature <= 0:
            raise ValueError("temperature must be greater than zero.")
        if not 0 < params.top_p <= 1:
            raise ValueError("top_p must be in the interval (0, 1].")

    def forecast_from_history(
        self,
        params: ForecastParameters,
        historical: pd.DataFrame,
        future_timestamps: Sequence[object] | pd.Series | pd.Index,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Run Kronos against an explicit historical cutoff without fetching newer bars.

        Replay/backtest callers pass the candle history that existed at the chosen
        cutoff plus the timestamps that followed it. The model never receives the
        realized future OHLCV values, which prevents look-ahead leakage while still
        letting a historical replay use the real future candle timestamps.
        """

        normalize_symbol(params.symbol)
        self._validate_params(params)
        if historical is None or historical.empty:
            raise ValueError("Historical replay context cannot be empty.")

        required = {"timestamp", "open", "high", "low", "close", "volume", "amount"}
        missing = sorted(required.difference(historical.columns))
        if missing:
            raise ValueError(f"Historical replay context is missing columns: {', '.join(missing)}")

        lookback = min(params.lookback, self.settings.max_context, len(historical))
        if lookback < 2:
            raise ValueError("Historical replay requires at least two completed candles.")
        history = historical.tail(lookback).copy().reset_index(drop=True)

        future_index = pd.to_datetime(list(future_timestamps), utc=True)
        if len(future_index) < params.pred_len:
            raise ValueError("Not enough future timestamps are available for the requested projection.")
        future_index = future_index[: params.pred_len]

        x_df = history[["open", "high", "low", "close", "volume", "amount"]].copy()
        x_timestamp = pd.Series(
            pd.to_datetime(history["timestamp"], utc=True),
            name="timestamp",
        )
        y_timestamp = pd.Series(future_index, name="timestamp")

        # Inference remains serialized so historical replay cannot race the live
        # forecast path for GPU memory/model state.
        with self._model_lock:
            prediction = self.predictor.predict(
                df=x_df,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=params.pred_len,
                T=params.temperature,
                top_k=params.top_k,
                top_p=params.top_p,
                sample_count=params.sample_count,
                verbose=False,
            )

        prediction = enforce_market_constraints(prediction)
        prediction.index.name = "timestamp"
        prediction = prediction.reset_index()
        return history, prediction

    def forecast(self, params: ForecastParameters) -> tuple[pd.DataFrame, pd.DataFrame]:
        symbol = normalize_symbol(params.symbol)
        self._validate_params(params)

        lookback = min(params.lookback, self.settings.max_context)
        candles = self.candles(symbol, params.timeframe, lookback)
        historical = candles.tail(lookback).reset_index(drop=True)
        future_timestamps = self.provider.future_timestamps(
            symbol=symbol,
            timeframe=params.timeframe,
            last_timestamp=historical["timestamp"].iloc[-1],
            periods=params.pred_len,
        )
        return self.forecast_from_history(params, historical, future_timestamps)


def enforce_market_constraints(frame: pd.DataFrame) -> pd.DataFrame:
    """Repair impossible decoded candles without changing their directional close."""
    clean = frame.copy()
    clean["high"] = clean[["high", "open", "close"]].max(axis=1)
    clean["low"] = clean[["low", "open", "close"]].min(axis=1)
    clean["volume"] = clean["volume"].clip(lower=0.0)
    clean["amount"] = clean["amount"].clip(lower=0.0)
    return clean