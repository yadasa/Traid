from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .forecast import ForecastEngine, ForecastParameters
from .market import get_timeframe, normalize_symbol
from .platform import ForecastPlatform, PlatformStore
from .providers import MarketDataError
from .service import app, get_engine, settings, store, trading_error
from .service_patch import NORMAL_SAMPLE_COUNT


ATR_PERIOD = 14


class KronosReplayRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    cutoff_ago: int | None = Field(default=120, ge=2, le=4500)
    cutoff_timestamp: datetime | None = None
    pred_len: int = Field(default=24, ge=1, le=200)
    advanced: bool = False
    paths: int | None = Field(default=None, ge=3, le=25)

    @model_validator(mode="after")
    def require_cutoff(self):
        if self.cutoff_timestamp is None and self.cutoff_ago is None:
            raise ValueError("Provide either cutoff_timestamp or cutoff_ago.")
        return self


def _as_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _wilder_atr(
    history: list[dict[str, Any]],
    period: int = ATR_PERIOD,
) -> float | None:
    """Return Wilder ATR using only candles that existed at the replay cutoff."""

    frame = pd.DataFrame(history).copy()
    if len(frame) < period:
        return None
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    if len(frame) < period:
        return None

    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    true_range = true_range.dropna().astype(float)
    if len(true_range) < period:
        return None

    atr = float(true_range.iloc[:period].mean())
    for value in true_range.iloc[period:]:
        atr = (atr * (period - 1) + float(value)) / period
    if pd.isna(atr) or atr <= 0:
        return None
    return atr


class _HistoricalSnapshotProvider:
    """Expose the real provider as it looked at one historical instant.

    ForecastPlatform.generate() and all of Traid's runtime patches continue calling
    the normal provider contract. This wrapper changes only data availability: a
    candle is visible when it had fully closed by the replay cutoff, and no forming
    candle is exposed because ordinary historical OHLC does not contain point-in-time
    intrabar states.
    """

    def __init__(self, provider: Any, cutoff: pd.Timestamp) -> None:
        self.provider = provider
        self.cutoff = _as_utc(cutoff)
        self.name = f"{getattr(provider, 'name', 'provider')}:historical_snapshot"
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _eligible(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (normalize_symbol(symbol), timeframe)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.copy()

        frame = self.provider.get_candles(key[0], timeframe, 5000)
        if frame is None or frame.empty:
            raise MarketDataError(
                f"No completed {timeframe} candles are available for {key[0]}."
            )

        tf = get_timeframe(timeframe)
        clean = frame.copy()
        opens = pd.to_datetime(clean["timestamp"], utc=True)
        closes = opens + pd.to_timedelta(tf.seconds, unit="s")
        clean = clean.loc[closes <= self.cutoff].copy().reset_index(drop=True)
        if clean.empty:
            raise MarketDataError(
                f"No {timeframe} candles for {key[0]} had closed by the replay cutoff."
            )

        self._cache[key] = clean
        return clean.copy()

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        frame = self._eligible(symbol, timeframe)
        return frame.tail(max(2, int(limit))).copy().reset_index(drop=True)

    def get_current_candle(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        normalize_symbol(symbol)
        get_timeframe(timeframe)
        return None

    def future_timestamps(
        self,
        symbol: str,
        timeframe: str,
        last_timestamp: pd.Timestamp,
        periods: int,
    ) -> pd.DatetimeIndex:
        return self.provider.future_timestamps(
            symbol=symbol,
            timeframe=timeframe,
            last_timestamp=last_timestamp,
            periods=periods,
        )


class _ReplayStore(PlatformStore):
    """Isolated copy of Traid's store whose event clock is the replay cutoff."""

    def __init__(self, path: str, cutoff: pd.Timestamp) -> None:
        self.replay_cutoff = _as_utc(cutoff)
        super().__init__(path)

    def events(
        self,
        start: str | None = None,
        end: str | None = None,
        impact: str | None = None,
    ) -> list[dict[str, Any]]:
        if start is not None or end is not None:
            start = (self.replay_cutoff - pd.Timedelta(hours=3)).isoformat()
            end = (self.replay_cutoff + pd.Timedelta(hours=3)).isoformat()
        return super().events(start=start, end=end, impact=impact)


def _copy_store(source: PlatformStore, destination: PlatformStore) -> None:
    """SQLite-consistent snapshot including WAL contents and learned calibration."""

    with source.connection() as source_connection:
        with destination.connection() as destination_connection:
            source_connection.backup(destination_connection)


def _generate_with_live_stack(
    *,
    engine: ForecastEngine,
    canonical: str,
    timeframe: str,
    cutoff: pd.Timestamp,
    pred_len: int,
    advanced: bool,
    paths: int | None,
) -> dict[str, Any]:
    """Call the exact ForecastPlatform.generate() currently used by the live chart."""

    with tempfile.TemporaryDirectory(prefix="traid-replay-") as directory:
        replay_store = _ReplayStore(
            str(Path(directory) / "traid-replay.db"),
            cutoff,
        )
        _copy_store(store, replay_store)
        replay_store.replay_cutoff = _as_utc(cutoff)

        snapshot_provider = _HistoricalSnapshotProvider(engine.provider, cutoff)
        snapshot_engine = ForecastEngine(
            settings=engine.settings,
            provider=snapshot_provider,
        )

        snapshot_engine._predictor = engine.predictor
        snapshot_engine._model_lock = engine._model_lock

        replay_platform = ForecastPlatform(snapshot_engine, replay_store)
        params = ForecastParameters(
            symbol=canonical,
            timeframe=timeframe,
            lookback=settings.default_lookback,
            pred_len=pred_len,
            temperature=1.0,
            top_k=0,
            top_p=0.9,
            sample_count=NORMAL_SAMPLE_COUNT,
        )

        return replay_platform.generate(
            params,
            advanced=advanced,
            paths=paths if advanced else None,
        )


@app.post("/v1/replay/kronos")
async def kronos_historical_replay(payload: KronosReplayRequest) -> dict[str, Any]:
    """Run Traid's normal forecast stack once at a no-lookahead historical cutoff."""

    try:
        canonical = normalize_symbol(payload.symbol)
        engine = get_engine()
        timeframe = get_timeframe(payload.timeframe)

        candles = await asyncio.to_thread(
            engine.candles,
            canonical,
            payload.timeframe,
            5000,
        )
        if candles is None or candles.empty:
            raise ValueError("No completed candles are available for replay.")

        candle_times = pd.to_datetime(candles["timestamp"], utc=True)
        close_times = candle_times + pd.to_timedelta(timeframe.seconds, unit="s")

        if payload.cutoff_timestamp is not None:
            simulated_present = _as_utc(payload.cutoff_timestamp)
            eligible_positions = [
                index
                for index, allowed in enumerate((close_times <= simulated_present).tolist())
                if allowed
            ]
            if not eligible_positions:
                raise ValueError(
                    "The selected replay time is earlier than the available candle history."
                )
            cutoff_index = eligible_positions[-1] + 1
        else:
            resolved_cutoff_ago = int(payload.cutoff_ago or 120)
            cutoff_index = len(candles) - resolved_cutoff_ago
            if cutoff_index <= 0:
                raise ValueError("The selected replay cutoff is outside available history.")
            simulated_present = _as_utc(close_times.iloc[cutoff_index - 1])

        if cutoff_index < 30:
            raise ValueError(
                "Not enough completed candles are available before that replay cutoff."
            )

        resolved_cutoff_ago = len(candles) - cutoff_index
        actual = (
            candles.iloc[cutoff_index : cutoff_index + payload.pred_len]
            .copy()
            .reset_index(drop=True)
        )

        started = time.perf_counter()
        result = await asyncio.to_thread(
            _generate_with_live_stack,
            engine=engine,
            canonical=canonical,
            timeframe=payload.timeframe,
            cutoff=simulated_present,
            pred_len=payload.pred_len,
            advanced=payload.advanced,
            paths=payload.paths,
        )
        inference_ms = (time.perf_counter() - started) * 1000

        history = list(result.get("history") or [])
        projection = list(result.get("projection") or [])
        if not history or not projection:
            raise ValueError("The live Traid forecast stack returned an empty replay projection.")

        cutoff_atr14 = _wilder_atr(history, ATR_PERIOD)
        base_close = float(history[-1]["close"])
        projected_close = float(projection[-1]["close"])
        actual_close = float(actual["close"].iloc[-1]) if not actual.empty else None

        def direction(value: float | None) -> str | None:
            if value is None:
                return None
            if value > base_close:
                return "bullish"
            if value < base_close:
                return "bearish"
            return "sideways"

        forecast_direction = direction(projected_close)
        actual_direction = direction(actual_close)
        first_projection_timestamp = _as_utc(projection[0]["timestamp"])
        last_known_open = _as_utc(history[-1]["timestamp"])
        last_known_close = last_known_open + pd.to_timedelta(timeframe.seconds, unit="s")

        actual_move_pct = None
        final_close_error_pct = None
        final_close_error_atr = None
        if actual_close is not None:
            actual_move_pct = (
                (actual_close - base_close) / max(abs(base_close), 1e-12) * 100
            )
            realized_index = min(len(actual), len(projection)) - 1
            matched_prediction_close = float(projection[realized_index]["close"])
            absolute_close_error = abs(matched_prediction_close - actual_close)
            final_close_error_pct = (
                absolute_close_error
                / max(abs(actual_close), 1e-12)
                * 100
            )
            if cutoff_atr14 is not None and cutoff_atr14 > 0:
                final_close_error_atr = absolute_close_error / cutoff_atr14

        parameters = dict(result.get("parameters") or {})
        revision = dict(result.get("revision") or {})
        ensemble = revision.get("path_ensemble") or {}

        return {
            "mode": "live_stack_single_cutoff",
            "forecast_logic": "exact_live_chart_stack",
            "symbol": canonical,
            "timeframe": payload.timeframe,
            "model": settings.model_id,
            "cutoff_ago": resolved_cutoff_ago,
            "requested_cutoff_timestamp": simulated_present.isoformat(),
            "cutoff_timestamp": first_projection_timestamp.isoformat(),
            "last_known_candle_timestamp": last_known_open.isoformat(),
            "last_known_candle_close_timestamp": last_known_close.isoformat(),
            "context_candles": len(history),
            "projection_candles": len(projection),
            "available_actual_candles": len(actual),
            "actual_horizon_complete": len(actual) >= payload.pred_len,
            "inference_ms": inference_ms,
            "advanced": bool(result.get("advanced", payload.advanced)),
            "feature_mode": result.get("feature_mode") or parameters.get("feature_mode"),
            "atr_period": ATR_PERIOD,
            "cutoff_atr14": cutoff_atr14,
            "parameters": parameters,
            "history": history,
            "projection": projection,
            "actual": actual.assign(
                timestamp=pd.to_datetime(actual["timestamp"], utc=True).map(
                    lambda value: value.isoformat()
                )
            ).to_dict(orient="records"),
            "uncertainty": result.get("uncertainty"),
            "revision": revision,
            "confidence": result.get("confidence"),
            "ict_context": result.get("ict_context") or revision.get("ict_context"),
            "context_model": result.get("context_model") or revision.get("context_model"),
            "regime_gate": result.get("regime_gate") or revision.get("regime_gate"),
            "replay_projection": {
                "aggregation": ensemble.get("aggregation"),
                "projection_path_index": ensemble.get("projection_path_index"),
                "projection_is_real_sample": ensemble.get("projection_is_real_sample"),
                "ict_ranked": ensemble.get("ict_ranked"),
                "paths": ensemble.get("paths") or parameters.get("sample_count"),
                "same_live_stack": True,
            },
            "summary": {
                "forecast_direction": forecast_direction,
                "actual_direction": actual_direction,
                "direction_correct": (
                    forecast_direction == actual_direction
                    if actual_direction is not None
                    else None
                ),
                "forecast_move_pct": (
                    (projected_close - base_close)
                    / max(abs(base_close), 1e-12)
                    * 100
                ),
                "actual_move_pct": actual_move_pct,
                "final_close_error_pct": final_close_error_pct,
                "final_close_error_atr": final_close_error_atr,
            },
        }
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc
