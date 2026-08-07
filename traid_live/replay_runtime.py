from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .forecast import ForecastParameters
from .market import get_timeframe, normalize_symbol
from .providers import MarketDataError
from .service import app, frame_records, get_engine, settings, trading_error


class KronosReplayRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    cutoff_ago: int | None = Field(default=120, ge=2, le=4500)
    cutoff_timestamp: datetime | None = None
    pred_len: int = Field(default=24, ge=1, le=200)

    @model_validator(mode="after")
    def require_cutoff(self):
        if self.cutoff_timestamp is None and self.cutoff_ago is None:
            raise ValueError("Provide either cutoff_timestamp or cutoff_ago.")
        return self


def _utc_timestamp(value: datetime) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def _as_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


@app.post("/v1/replay/kronos")
async def kronos_historical_replay(payload: KronosReplayRequest) -> dict[str, Any]:
    """Generate one no-lookahead Kronos forecast at a historical candle cutoff.

    Kronos runs once using only candles fully completed by the simulated present.
    Realized candles after the cutoff are returned separately for cheap playback.
    A replay is allowed near the present even when fewer realized candles exist
    than the requested forecast horizon; the model still generates its full horizon
    and the client reveals only the future candles that have actually closed so far.
    """

    try:
        canonical = normalize_symbol(payload.symbol)
        engine = get_engine()
        timeframe = get_timeframe(payload.timeframe)

        if payload.cutoff_timestamp is not None:
            requested = 5000
        else:
            requested = min(
                5000,
                int(payload.cutoff_ago or 120) + settings.max_context + payload.pred_len + 2,
            )

        candles = await asyncio.to_thread(
            engine.candles,
            canonical,
            payload.timeframe,
            requested,
        )
        if candles is None or candles.empty:
            raise ValueError("No completed candles are available for replay.")

        candle_times = pd.to_datetime(candles["timestamp"], utc=True)
        selected_cutoff: pd.Timestamp | None = None

        if payload.cutoff_timestamp is not None:
            selected_cutoff = _utc_timestamp(payload.cutoff_timestamp)
            close_times = candle_times + pd.to_timedelta(timeframe.seconds, unit="s")
            eligible_positions = [
                index
                for index, allowed in enumerate((close_times <= selected_cutoff).tolist())
                if allowed
            ]
            if not eligible_positions:
                raise ValueError(
                    "The selected replay time is earlier than the available candle history."
                )
            cutoff_index = eligible_positions[-1] + 1
            resolved_cutoff_ago = len(candles) - cutoff_index
        else:
            resolved_cutoff_ago = int(payload.cutoff_ago or 120)
            cutoff_index = len(candles) - resolved_cutoff_ago

        if cutoff_index < 30:
            raise ValueError(
                "Not enough completed candles are available before that replay cutoff."
            )

        history_available = candles.iloc[:cutoff_index].copy().reset_index(drop=True)
        context = history_available.tail(settings.max_context).copy().reset_index(drop=True)
        if context.empty:
            raise ValueError("No model context is available before that replay cutoff.")

        # Forecast timestamps are generated independently of realized candles.
        # This is what makes a cutoff close to the present valid: Kronos can still
        # forecast 24 candles even if only 23 of those candles have closed so far.
        future_timestamps = engine.provider.future_timestamps(
            symbol=canonical,
            timeframe=payload.timeframe,
            last_timestamp=context["timestamp"].iloc[-1],
            periods=payload.pred_len,
        )

        # Actual future is intentionally partial when the replay horizon extends
        # beyond the newest completed broker candle. Playback simply stops there.
        actual = (
            candles.iloc[cutoff_index : cutoff_index + payload.pred_len]
            .copy()
            .reset_index(drop=True)
        )

        params = ForecastParameters(
            symbol=canonical,
            timeframe=payload.timeframe,
            lookback=len(context),
            pred_len=payload.pred_len,
            temperature=1.0,
            top_k=0,
            top_p=0.9,
            sample_count=10,
        )

        started = time.perf_counter()
        used_history, prediction = await asyncio.to_thread(
            engine.forecast_from_history,
            params,
            context,
            future_timestamps,
        )
        inference_ms = (time.perf_counter() - started) * 1000

        base_close = float(used_history["close"].iloc[-1])
        projected_close = float(prediction["close"].iloc[-1])
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

        last_known_open = _as_utc(used_history["timestamp"].iloc[-1])
        last_known_close = last_known_open + pd.to_timedelta(timeframe.seconds, unit="s")
        forecast_boundary = _as_utc(future_timestamps[0])

        actual_move_pct = None
        final_close_error_pct = None
        if actual_close is not None:
            actual_move_pct = (
                (actual_close - base_close) / max(abs(base_close), 1e-12) * 100
            )
            # When only part of the forecast horizon has happened, compare the
            # latest realized candle with the prediction at the same horizon index.
            realized_index = min(len(actual), len(prediction)) - 1
            matched_prediction_close = float(prediction["close"].iloc[realized_index])
            final_close_error_pct = (
                abs(matched_prediction_close - actual_close)
                / max(abs(actual_close), 1e-12)
                * 100
            )

        return {
            "mode": "kronos_single_cutoff",
            "symbol": canonical,
            "timeframe": payload.timeframe,
            "model": settings.model_id,
            "cutoff_ago": resolved_cutoff_ago,
            "requested_cutoff_timestamp": (
                selected_cutoff.isoformat() if selected_cutoff is not None else None
            ),
            "cutoff_timestamp": forecast_boundary.isoformat(),
            "last_known_candle_timestamp": last_known_open.isoformat(),
            "last_known_candle_close_timestamp": last_known_close.isoformat(),
            "context_candles": len(used_history),
            "projection_candles": len(prediction),
            "available_actual_candles": len(actual),
            "actual_horizon_complete": len(actual) >= payload.pred_len,
            "inference_ms": inference_ms,
            "parameters": {
                "lookback": len(used_history),
                "pred_len": payload.pred_len,
                "temperature": params.temperature,
                "top_k": params.top_k,
                "top_p": params.top_p,
                "sample_count": params.sample_count,
            },
            "history": frame_records(used_history),
            "projection": frame_records(prediction),
            "actual": frame_records(actual),
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
            },
        }
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc
