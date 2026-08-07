from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
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
    def require_realized_future(self):
        if self.cutoff_timestamp is None:
            if self.cutoff_ago is None:
                raise ValueError("Provide either cutoff_timestamp or cutoff_ago.")
            if self.cutoff_ago < self.pred_len:
                raise ValueError("Cutoff candles ago must be at least as large as the projection length.")
        return self


def _utc_timestamp(value: datetime) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


@app.post("/v1/replay/kronos")
async def kronos_historical_replay(payload: KronosReplayRequest) -> dict[str, Any]:
    """Generate one no-lookahead Kronos forecast at a historical candle cutoff.

    The model runs once. Realized candles after the cutoff are returned separately
    for cheap client-side playback and never enter the model input.

    An exact cutoff timestamp represents the simulated present. Only candles that
    had fully closed by that instant are eligible for model context. For example,
    selecting 14:30 on a 5-minute chart includes the 14:25 candle (which closes at
    14:30) but excludes the 14:30 candle that has only just opened.
    """

    try:
        canonical = normalize_symbol(payload.symbol)
        engine = get_engine()
        timeframe = get_timeframe(payload.timeframe)

        # Timestamp selection needs a broad completed-candle window so the client
        # can choose a concrete date/time instead of converting it to candles-ago.
        # Legacy candles-ago requests retain the smaller bounded fetch.
        if payload.cutoff_timestamp is not None:
            requested = 5000
        else:
            requested = min(5000, int(payload.cutoff_ago or 120) + settings.max_context + 2)

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
            eligible = close_times <= selected_cutoff
            eligible_positions = [index for index, allowed in enumerate(eligible.tolist()) if allowed]
            if not eligible_positions:
                raise ValueError("The selected replay time is earlier than the available candle history.")
            cutoff_index = eligible_positions[-1] + 1
            resolved_cutoff_ago = len(candles) - cutoff_index
        else:
            resolved_cutoff_ago = int(payload.cutoff_ago or 120)
            cutoff_index = len(candles) - resolved_cutoff_ago

        if cutoff_index < 30:
            raise ValueError("Not enough completed candles are available before that replay cutoff.")
        if resolved_cutoff_ago < payload.pred_len:
            raise ValueError(
                "The selected replay time is too recent for that projection length; "
                "choose an earlier time or fewer projection candles."
            )

        history_available = candles.iloc[:cutoff_index].copy().reset_index(drop=True)
        context = history_available.tail(settings.max_context).copy().reset_index(drop=True)
        actual = (
            candles.iloc[cutoff_index : cutoff_index + payload.pred_len]
            .copy()
            .reset_index(drop=True)
        )
        if len(actual) < payload.pred_len:
            raise ValueError(
                "Not enough completed candles exist after the selected cutoff for that projection length."
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
            actual["timestamp"],
        )
        inference_ms = (time.perf_counter() - started) * 1000

        base_close = float(used_history["close"].iloc[-1])
        projected_close = float(prediction["close"].iloc[-1])
        actual_close = float(actual["close"].iloc[-1])

        def direction(value: float) -> str:
            if value > base_close:
                return "bullish"
            if value < base_close:
                return "bearish"
            return "sideways"

        last_known_open = pd.Timestamp(used_history["timestamp"].iloc[-1])
        if last_known_open.tzinfo is None:
            last_known_open = last_known_open.tz_localize("UTC")
        else:
            last_known_open = last_known_open.tz_convert("UTC")
        last_known_close = last_known_open + pd.to_timedelta(timeframe.seconds, unit="s")
        forecast_boundary = pd.Timestamp(actual["timestamp"].iloc[0])
        if forecast_boundary.tzinfo is None:
            forecast_boundary = forecast_boundary.tz_localize("UTC")
        else:
            forecast_boundary = forecast_boundary.tz_convert("UTC")

        return {
            "mode": "kronos_single_cutoff",
            "symbol": canonical,
            "timeframe": payload.timeframe,
            "model": settings.model_id,
            "cutoff_ago": resolved_cutoff_ago,
            "requested_cutoff_timestamp": (
                selected_cutoff.isoformat()
                if selected_cutoff is not None
                else None
            ),
            "cutoff_timestamp": forecast_boundary.isoformat(),
            "last_known_candle_timestamp": last_known_open.isoformat(),
            "last_known_candle_close_timestamp": last_known_close.isoformat(),
            "context_candles": len(used_history),
            "projection_candles": len(prediction),
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
                "forecast_direction": direction(projected_close),
                "actual_direction": direction(actual_close),
                "direction_correct": direction(projected_close) == direction(actual_close),
                "forecast_move_pct": (
                    (projected_close - base_close) / max(abs(base_close), 1e-12) * 100
                ),
                "actual_move_pct": (
                    (actual_close - base_close) / max(abs(base_close), 1e-12) * 100
                ),
                "final_close_error_pct": (
                    abs(projected_close - actual_close)
                    / max(abs(actual_close), 1e-12)
                    * 100
                ),
            },
        }
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc
