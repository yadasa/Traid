from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .forecast import ForecastParameters
from .market import normalize_symbol
from .providers import MarketDataError
from .service import app, frame_records, get_engine, settings, trading_error


class KronosReplayRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    cutoff_ago: int = Field(default=120, ge=2, le=4500)
    pred_len: int = Field(default=24, ge=1, le=200)

    @model_validator(mode="after")
    def require_realized_future(self):
        if self.cutoff_ago < self.pred_len:
            raise ValueError("Cutoff candles ago must be at least as large as the projection length.")
        return self


@app.post("/v1/replay/kronos")
async def kronos_historical_replay(payload: KronosReplayRequest) -> dict[str, Any]:
    """Generate one no-lookahead Kronos forecast at a historical candle cutoff.

    The model runs once. Realized candles after the cutoff are returned separately
    for cheap client-side playback and never enter the model input.
    """

    try:
        canonical = normalize_symbol(payload.symbol)
        engine = get_engine()

        # Fetch one contiguous completed-candle window. The final `cutoff_ago`
        # candles are the hidden/replay future; everything before them is eligible
        # model context. Kronos itself can only consume `max_context` candles.
        requested = min(5000, payload.cutoff_ago + settings.max_context + 2)
        candles = await asyncio.to_thread(
            engine.candles,
            canonical,
            payload.timeframe,
            requested,
        )
        cutoff_index = len(candles) - payload.cutoff_ago
        if cutoff_index < 30:
            raise ValueError(
                "Not enough completed candles are available before that replay cutoff."
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

        return {
            "mode": "kronos_single_cutoff",
            "symbol": canonical,
            "timeframe": payload.timeframe,
            "model": settings.model_id,
            "cutoff_ago": payload.cutoff_ago,
            "cutoff_timestamp": used_history["timestamp"].iloc[-1].isoformat(),
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
