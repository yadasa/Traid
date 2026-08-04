from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, replace
from typing import Any, Sequence

import pandas as pd
from fastapi.routing import APIWebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .forecast import ForecastParameters
from .platform import ForecastPlatform, PlatformStore
from .service import app, store, stream as original_stream


NORMAL_SAMPLE_COUNT = 10
ADVANCED_PATH_COUNT = 14
ALIGNED_TIMEFRAMES = ("5m", "15m", "1h")
ONE_HOUR_HORIZONS = {"5m": 12, "15m": 4, "1h": 1}

_original_generate = ForecastPlatform.generate
_original_forecasts = PlatformStore.forecasts
_original_forecast = PlatformStore.forecast


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _direction(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "bullish"
    if value < -threshold:
        return "bearish"
    return "sideways"


def _true_ranges(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _market_context(history: Sequence[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(history).copy()
    if len(frame) < 5:
        return {
            "regime": "unknown",
            "trend": {"direction": "unknown", "strength_pct": 0.0},
            "range": {"state": "unknown", "position_pct": None, "width_pct": None},
            "volatility": {"state": "unknown", "atr_pct": None, "relative_pct": None},
        }

    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    close = frame["close"]
    price = max(abs(float(close.iloc[-1])), 1e-12)
    ema20 = close.ewm(span=min(20, len(frame)), adjust=False).mean()
    ema50 = close.ewm(span=min(50, len(frame)), adjust=False).mean()
    true_range = _true_ranges(frame)
    atr14 = float(true_range.tail(min(14, len(true_range))).mean())
    atr_pct = atr14 / price * 100

    trend_gap = float(ema20.iloc[-1] - ema50.iloc[-1])
    slope_window = min(8, len(ema20) - 1)
    trend_slope = float(ema20.iloc[-1] - ema20.iloc[-1 - slope_window]) if slope_window else 0.0
    trend_threshold = max(atr14 * 0.12, price * 0.00002)
    trend_direction = _direction(trend_gap + trend_slope * 0.35, trend_threshold)
    trend_strength = _clamp(
        abs(trend_gap) / max(atr14, 1e-12) * 45
        + abs(trend_slope) / max(atr14, 1e-12) * 20,
        0,
        100,
    )

    range_frame = frame.tail(min(20, len(frame)))
    range_low = float(range_frame["low"].min())
    range_high = float(range_frame["high"].max())
    range_width = max(range_high - range_low, 1e-12)
    range_position = _clamp((float(close.iloc[-1]) - range_low) / range_width * 100, 0, 100)
    range_width_pct = range_width / price * 100
    if range_position >= 92:
        range_state = "testing upper boundary"
    elif range_position <= 8:
        range_state = "testing lower boundary"
    elif trend_strength < 28:
        range_state = "range-bound"
    else:
        range_state = "inside range"

    baseline = float(true_range.tail(min(60, len(true_range))).median())
    relative_volatility = atr14 / max(baseline, 1e-12) * 100
    if relative_volatility >= 145:
        volatility_state = "high"
    elif relative_volatility <= 70:
        volatility_state = "low"
    else:
        volatility_state = "normal"

    if volatility_state == "high":
        regime = "volatile"
    elif trend_strength >= 45 and trend_direction != "sideways":
        regime = "trending"
    else:
        regime = "ranging"

    return {
        "regime": regime,
        "trend": {
            "direction": trend_direction,
            "strength_pct": round(trend_strength, 1),
            "ema_gap_pct": round(trend_gap / price * 100, 4),
        },
        "range": {
            "state": range_state,
            "position_pct": round(range_position, 1),
            "width_pct": round(range_width_pct, 4),
            "low": range_low,
            "high": range_high,
        },
        "volatility": {
            "state": volatility_state,
            "atr_pct": round(atr_pct, 4),
            "relative_pct": round(relative_volatility, 1),
        },
    }


def _confidence_for_item(
    item: dict[str, Any],
    accuracy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    history = item.get("history") or []
    projection = item.get("projection") or []
    uncertainty = item.get("uncertainty") or {}
    revision = item.get("revision") or {}
    parameters = item.get("parameters") or {}
    timeframe = item.get("timeframe") or parameters.get("timeframe") or "5m"

    if not history or not projection:
        return {
            "score_pct": 50.0,
            "grade": "low",
            "calibrated": False,
            "sample_count": int(parameters.get("sample_count") or NORMAL_SAMPLE_COUNT),
            "paths": int(uncertainty.get("paths") or 1),
        }

    context = _market_context(history)
    base = float(history[-1]["close"])
    horizon = min(ONE_HOUR_HORIZONS.get(timeframe, len(projection)), len(projection))
    target = float(projection[horizon - 1]["close"])
    move_pct = (target - base) / max(abs(base), 1e-12) * 100
    forecast_direction = _direction(move_pct)

    probabilities = uncertainty.get("bullish_probability") or []
    if probabilities:
        probability = float(probabilities[min(horizon - 1, len(probabilities) - 1)])
        path_agreement = max(probability, 100 - probability)
    else:
        path_agreement = 55.0

    stability = float(revision.get("stability_score", 58.0))
    samples = int((accuracy or {}).get("samples") or 0)
    realized_accuracy = (accuracy or {}).get("direction_accuracy")
    historical = 50.0 if realized_accuracy is None else float(realized_accuracy)
    if samples < 20:
        historical = 50 + (historical - 50) * samples / 20

    trend_direction = context["trend"]["direction"]
    if forecast_direction == "sideways" or trend_direction in {"sideways", "unknown"}:
        trend_alignment = 52.0
    elif forecast_direction == trend_direction:
        trend_alignment = 68.0
    else:
        trend_alignment = 34.0

    atr_pct = float(context["volatility"].get("atr_pct") or 0.0)
    move_to_atr = abs(move_pct) / max(atr_pct, 1e-9)
    if 0.25 <= move_to_atr <= 2.5:
        magnitude_quality = 66.0
    elif move_to_atr < 0.1:
        magnitude_quality = 42.0
    elif move_to_atr > 5:
        magnitude_quality = 38.0
    else:
        magnitude_quality = 54.0

    score = (
        path_agreement * 0.35
        + stability * 0.20
        + historical * 0.20
        + trend_alignment * 0.15
        + magnitude_quality * 0.10
    )
    score = round(_clamp(score, 35.0, 85.0), 1)
    grade = "high" if score >= 70 else "medium" if score >= 58 else "low"
    return {
        "score_pct": score,
        "grade": grade,
        "calibrated": False,
        "direction": forecast_direction,
        "one_hour_move_pct": round(move_pct, 4),
        "sample_count": int(parameters.get("sample_count") or NORMAL_SAMPLE_COUNT),
        "paths": int(uncertainty.get("paths") or 1),
        "components": {
            "path_agreement_pct": round(path_agreement, 1),
            "stability_pct": round(stability, 1),
            "historical_accuracy_pct": round(historical, 1),
            "trend_alignment_pct": round(trend_alignment, 1),
            "magnitude_quality_pct": round(magnitude_quality, 1),
        },
    }


def _enhance_item(
    item: dict[str, Any],
    accuracy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enhanced = dict(item)
    confidence = enhanced.get("confidence") or _confidence_for_item(enhanced, accuracy)
    enhanced["confidence"] = confidence
    revision = dict(enhanced.get("revision") or {})
    revision["model_confidence"] = confidence
    enhanced["revision"] = revision
    if enhanced.get("uncertainty"):
        uncertainty = dict(enhanced["uncertainty"])
        uncertainty["confidence"] = confidence
        enhanced["uncertainty"] = uncertainty
    return enhanced


def forecasts_with_confidence(
    self: PlatformStore,
    symbol: str,
    timeframe: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    items = _original_forecasts(self, symbol, timeframe, limit)
    accuracy = self.accuracy(symbol, timeframe)
    return [_enhance_item(item, accuracy) for item in items]


def forecast_with_confidence(
    self: PlatformStore,
    forecast_id: str,
) -> dict[str, Any] | None:
    item = _original_forecast(self, forecast_id)
    if not item:
        return None
    accuracy = self.accuracy(item["symbol"], item["timeframe"])
    return _enhance_item(item, accuracy)


def generate_with_confidence(
    self: ForecastPlatform,
    params: ForecastParameters,
    *,
    advanced: bool = False,
    paths: int | None = None,
) -> dict[str, Any]:
    effective = replace(params, sample_count=max(NORMAL_SAMPLE_COUNT, int(params.sample_count)))
    effective_paths = max(ADVANCED_PATH_COUNT, int(paths or 0)) if advanced else paths
    result = _original_generate(self, effective, advanced=advanced, paths=effective_paths)
    result["parameters"] = asdict(effective)
    accuracy = self.store.accuracy(result["symbol"], result["timeframe"])
    confidence = _confidence_for_item(result, accuracy)
    result["confidence"] = confidence

    revision = dict(result.get("revision") or {})
    revision["model_confidence"] = confidence
    result["revision"] = revision
    uncertainty = result.get("uncertainty")
    if uncertainty:
        uncertainty = dict(uncertainty)
        uncertainty["confidence"] = confidence
        result["uncertainty"] = uncertainty

    with self.store._lock, self.store.connection() as conn:
        conn.execute(
            "UPDATE forecasts SET parameters_json=?, revision_json=?, uncertainty_json=? WHERE id=?",
            (
                json.dumps(asdict(effective), separators=(",", ":")),
                json.dumps(revision, separators=(",", ":")),
                json.dumps(uncertainty, separators=(",", ":")) if uncertainty else None,
                result["id"],
            ),
        )
    return result


def aligned_consensus(
    self: ForecastPlatform,
    symbol: str,
    selected_timeframe: str,
) -> dict[str, Any]:
    readings: list[dict[str, Any]] = []
    histories: dict[str, Sequence[dict[str, Any]]] = {}

    for timeframe in ALIGNED_TIMEFRAMES:
        latest = self.store.forecasts(symbol, timeframe, 1)
        if not latest:
            readings.append(
                {
                    "timeframe": timeframe,
                    "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
                    "direction": "unknown",
                    "move_pct": None,
                    "confidence_pct": None,
                }
            )
            continue

        forecast = latest[0]
        history = forecast.get("history") or []
        projection = forecast.get("projection") or []
        histories[timeframe] = history
        if not history or not projection:
            readings.append(
                {
                    "timeframe": timeframe,
                    "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
                    "direction": "unknown",
                    "move_pct": None,
                    "confidence_pct": None,
                }
            )
            continue

        horizon = min(ONE_HOUR_HORIZONS[timeframe], len(projection))
        base = float(history[-1]["close"])
        target = float(projection[horizon - 1]["close"])
        move_pct = (target - base) / max(abs(base), 1e-12) * 100
        context = _market_context(history)
        neutral_threshold = max(float(context["volatility"].get("atr_pct") or 0.0) * 0.15, 0.002)
        readings.append(
            {
                "timeframe": timeframe,
                "horizon_candles": horizon,
                "target_window": "1h",
                "direction": _direction(move_pct, neutral_threshold),
                "move_pct": round(move_pct, 4),
                "confidence_pct": forecast.get("confidence", {}).get("score_pct"),
                "forecast_id": forecast["id"],
            }
        )

    directional = [
        row["direction"]
        for row in readings
        if row["direction"] in {"bullish", "bearish"}
    ]
    has_bullish = "bullish" in directional
    has_bearish = "bearish" in directional
    contradiction = has_bullish and has_bearish
    dominant = max(set(directional), key=directional.count) if directional else "sideways"
    agreement_pct = directional.count(dominant) / len(directional) * 100 if directional else 0.0
    complete = all(row["direction"] != "unknown" for row in readings)
    aligned = complete and not contradiction and agreement_pct >= 66.666
    trade_bias = dominant if aligned else "no_trade"

    confidence_values = [
        float(row["confidence_pct"])
        for row in readings
        if row.get("confidence_pct") is not None
    ]
    average_confidence = statistics.fmean(confidence_values) if confidence_values else 50.0
    aligned_confidence = round(
        _clamp(average_confidence * (0.65 + 0.35 * agreement_pct / 100), 30, 85),
        1,
    )

    context_history = histories.get("15m") or histories.get(selected_timeframe) or next(iter(histories.values()), [])
    return {
        "selected": selected_timeframe,
        "target_window": "1h",
        "readings": readings,
        "agreement_pct": round(agreement_pct, 1),
        "consensus": dominant if not contradiction else "conflict",
        "aligned": aligned,
        "contradiction": contradiction,
        "alignment_status": "aligned" if aligned else "conflict" if contradiction else "incomplete",
        "trade_bias": trade_bias,
        "confidence_pct": aligned_confidence,
        "market_context": _market_context(context_history),
    }


PlatformStore.forecasts = forecasts_with_confidence  # type: ignore[method-assign]
PlatformStore.forecast = forecast_with_confidence  # type: ignore[method-assign]
ForecastPlatform.generate = generate_with_confidence  # type: ignore[method-assign]
ForecastPlatform.consensus = aligned_consensus  # type: ignore[method-assign]

if int(store.get_setting("uncertainty_paths", ADVANCED_PATH_COUNT) or 0) < ADVANCED_PATH_COUNT:
    store.set_setting("uncertainty_paths", ADVANCED_PATH_COUNT, actor="system:upgrade")


app.router.routes = [
    route
    for route in app.router.routes
    if not (
        isinstance(route, APIWebSocketRoute)
        and route.path == "/v1/stream/{symbol}"
    )
]


@app.websocket("/v1/stream/{symbol}")
async def safe_stream(
    websocket: WebSocket,
    symbol: str,
    timeframe: str = "5m",
    with_forecast: bool = False,
    advanced: bool = False,
    pred_len: int = 24,
) -> None:
    try:
        await original_stream(
            websocket=websocket,
            symbol=symbol,
            timeframe=timeframe,
            with_forecast=with_forecast,
            advanced=advanced,
            pred_len=pred_len,
        )
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        message = str(exc).lower()
        if "close message has been sent" in message or (
            "websocket" in message and "closed" in message
        ):
            return
        raise
