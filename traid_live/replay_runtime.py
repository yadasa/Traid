from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .forecast import ForecastParameters
from .intelligence_v2 import _predict_paths
from .market import INDEX_SYMBOLS, get_timeframe, normalize_symbol
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


def _representative_sample(
    paths: list[pd.DataFrame],
    *,
    base_close: float,
) -> tuple[int, pd.DataFrame, dict[str, float]]:
    """Choose one real Kronos sample close to the ensemble center.

    KronosPredictor.predict() averages decoded samples into a synthetic candle path.
    That is undesirable for replay because independently averaged OHLC values can
    exaggerate inter-candle gaps. Replay instead preserves every sampled path and
    selects the actual sample nearest the ensemble median while mildly preferring
    paths whose opens remain connected to the preceding close.
    """

    if not paths:
        raise ValueError("Kronos returned no sampled replay paths.")

    close_matrix = np.asarray(
        [path["close"].to_numpy(dtype=float) for path in paths],
        dtype=float,
    )
    median_close = np.median(close_matrix, axis=0)
    scale = max(abs(float(base_close)), 1e-9)

    scores: list[float] = []
    gap_scores: list[float] = []
    center_scores: list[float] = []
    for path in paths:
        closes = path["close"].to_numpy(dtype=float)
        opens = path["open"].to_numpy(dtype=float)
        previous = np.concatenate(([float(base_close)], closes[:-1]))
        gaps = np.abs(opens - previous) / scale
        center = np.sqrt(np.mean(((closes - median_close) / scale) ** 2))
        gap = float(np.mean(gaps))
        anchor = abs(float(opens[0]) - float(base_close)) / scale
        # Ensemble-center distance dominates. Gap/anchor terms only break ties
        # toward a more physically continuous real sample.
        score = float(center + gap * 0.35 + anchor * 0.20)
        center_scores.append(float(center))
        gap_scores.append(gap)
        scores.append(score)

    index = int(np.argmin(np.asarray(scores, dtype=float)))
    return (
        index,
        paths[index].copy().reset_index(drop=True),
        {
            "score": scores[index],
            "center_distance": center_scores[index],
            "mean_raw_gap_pct": gap_scores[index] * 100.0,
        },
    )


def _stitch_projection(
    projection: pd.DataFrame,
    *,
    base_close: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Make forecast candles sequential without changing each candle's shape.

    Decoded Kronos candles can contain an open that differs materially from the
    previous decoded close. On an intraday CFD replay that produces visual gaps and
    can make the price scale explode. Shift the complete OHLC candle by that gap so
    every candle opens at the previous close. The candle body delta and wick
    distances are preserved exactly; only the absolute inter-candle discontinuity
    is removed.
    """

    clean = projection.copy().reset_index(drop=True)
    if clean.empty:
        return clean, {"max_shift_pct": 0.0, "mean_shift_pct": 0.0}

    previous_close = float(base_close)
    shifts: list[float] = []
    scale = max(abs(float(base_close)), 1e-9)

    for index in range(len(clean)):
        original_open = float(clean.at[index, "open"])
        shift = previous_close - original_open
        shifts.append(abs(shift) / scale * 100.0)
        for column in ("open", "high", "low", "close"):
            clean.at[index, column] = float(clean.at[index, column]) + shift

        open_price = float(clean.at[index, "open"])
        close_price = float(clean.at[index, "close"])
        clean.at[index, "high"] = max(
            float(clean.at[index, "high"]),
            open_price,
            close_price,
        )
        clean.at[index, "low"] = min(
            float(clean.at[index, "low"]),
            open_price,
            close_price,
        )
        previous_close = close_price

    return clean, {
        "max_shift_pct": max(shifts) if shifts else 0.0,
        "mean_shift_pct": float(np.mean(shifts)) if shifts else 0.0,
    }


def _sample_replay_projection(
    engine: Any,
    *,
    symbol: str,
    context: pd.DataFrame,
    future_timestamps: pd.DatetimeIndex,
    params: ForecastParameters,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run replay inference using the same sampled-path conventions as live mode."""

    used_history = context.tail(min(params.lookback, settings.max_context)).copy().reset_index(drop=True)
    model_input = used_history[
        ["open", "high", "low", "close", "volume", "amount"]
    ].copy()

    feature_mode = "ohlcv"
    if symbol in INDEX_SYMBOLS:
        # Broker tick volume on index CFDs is not comparable to the data Kronos was
        # trained to interpret as real volume. Live Traid already uses price-only
        # inference for these markets; replay must do the same.
        model_input["volume"] = 0.0
        model_input["amount"] = 0.0
        feature_mode = "price_only"

    x_timestamp = pd.Series(
        pd.to_datetime(used_history["timestamp"], utc=True),
        name="timestamp",
    )
    y_timestamp = pd.Series(
        pd.to_datetime(future_timestamps, utc=True),
        name="timestamp",
    )

    paths = _predict_paths(
        engine,
        model_input,
        x_timestamp,
        y_timestamp,
        params,
        params.sample_count,
    )
    base_close = float(used_history["close"].iloc[-1])
    sample_index, raw_sample, sample_meta = _representative_sample(
        paths,
        base_close=base_close,
    )
    projection, continuity_meta = _stitch_projection(
        raw_sample,
        base_close=base_close,
    )

    return used_history, projection, {
        "feature_mode": feature_mode,
        "sample_selection": "real_medoid_near_ensemble_median",
        "selected_sample_index": sample_index,
        "sample_count": len(paths),
        "continuity_rebased": True,
        "representative": sample_meta,
        "continuity": continuity_meta,
        "raw_selected_projection": frame_records(raw_sample),
    }


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

        future_timestamps = engine.provider.future_timestamps(
            symbol=canonical,
            timeframe=payload.timeframe,
            last_timestamp=context["timestamp"].iloc[-1],
            periods=payload.pred_len,
        )

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
        used_history, prediction, replay_meta = await asyncio.to_thread(
            _sample_replay_projection,
            engine,
            symbol=canonical,
            context=context,
            future_timestamps=future_timestamps,
            params=params,
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
            "feature_mode": replay_meta["feature_mode"],
            "replay_projection": {
                "sample_selection": replay_meta["sample_selection"],
                "selected_sample_index": replay_meta["selected_sample_index"],
                "sample_count": replay_meta["sample_count"],
                "continuity_rebased": replay_meta["continuity_rebased"],
                "representative": replay_meta["representative"],
                "continuity": replay_meta["continuity"],
            },
            "parameters": {
                "lookback": len(used_history),
                "pred_len": payload.pred_len,
                "temperature": params.temperature,
                "top_k": params.top_k,
                "top_p": params.top_p,
                "sample_count": params.sample_count,
                "feature_mode": replay_meta["feature_mode"],
                "sample_selection": replay_meta["sample_selection"],
                "continuity_rebased": True,
            },
            "history": frame_records(used_history),
            "projection": frame_records(prediction),
            "projection_raw_selected_sample": replay_meta["raw_selected_projection"],
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
