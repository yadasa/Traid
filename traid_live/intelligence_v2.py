from __future__ import annotations

import json
import math
import statistics
import threading
from dataclasses import asdict, replace
from typing import Any, Sequence

import numpy as np
import pandas as pd
import torch

from model.kronos import sample_from_logits

from .forecast import ForecastParameters
from .market import INDEX_SYMBOLS, normalize_symbol
from .platform import (
    ForecastPlatform,
    PlatformStore,
    revision_metrics,
    utc_now_iso,
)
from .service import get_platform
from .service_patch import ADVANCED_PATH_COUNT, NORMAL_SAMPLE_COUNT


MIN_CALIBRATION_FORECASTS = 30
ONE_HOUR_HORIZONS = {"5m": 12, "15m": 4, "1h": 1}
ALIGNED_TIMEFRAMES = ("5m", "15m", "1h")
_PATH_BATCH_SIZE = 7

_GENERATION_GUARD = threading.Lock()
_GENERATION_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _direction(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "bullish"
    if value < -threshold:
        return "bearish"
    return "sideways"


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output = frame.copy()
    output["timestamp"] = pd.to_datetime(output["timestamp"], utc=True).map(
        lambda value: value.isoformat()
    )
    return output.to_dict(orient="records")


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _percentile(values: Sequence[float], quantile: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), quantile))


def _time_features(timestamps: pd.Series) -> np.ndarray:
    values = pd.to_datetime(timestamps, utc=True)
    return np.column_stack(
        [
            values.dt.minute,
            values.dt.hour,
            values.dt.weekday,
            values.dt.day,
            values.dt.month,
        ]
    ).astype(np.float32)


def _autoregressive_samples(
    predictor: Any,
    x: np.ndarray,
    x_stamp: np.ndarray,
    y_stamp: np.ndarray,
    *,
    pred_len: int,
    temperature: float,
    top_k: int,
    top_p: float,
    sample_count: int,
) -> np.ndarray:
    """Run one batched Kronos decode and preserve every sampled path."""

    x_tensor = torch.from_numpy(x.astype(np.float32)).to(predictor.device)
    x_stamp_tensor = torch.from_numpy(x_stamp.astype(np.float32)).to(predictor.device)
    y_stamp_tensor = torch.from_numpy(y_stamp.astype(np.float32)).to(predictor.device)

    with torch.no_grad():
        x_tensor = torch.clip(x_tensor, -predictor.clip, predictor.clip)
        x_tensor = (
            x_tensor.unsqueeze(1)
            .repeat(1, sample_count, 1, 1)
            .reshape(-1, x_tensor.size(1), x_tensor.size(2))
        )
        x_stamp_tensor = (
            x_stamp_tensor.unsqueeze(1)
            .repeat(1, sample_count, 1, 1)
            .reshape(-1, x_stamp_tensor.size(1), x_stamp_tensor.size(2))
        )
        y_stamp_tensor = (
            y_stamp_tensor.unsqueeze(1)
            .repeat(1, sample_count, 1, 1)
            .reshape(-1, y_stamp_tensor.size(1), y_stamp_tensor.size(2))
        )

        x_token = predictor.tokenizer.encode(x_tensor, half=True)
        initial_seq_len = x_tensor.size(1)
        total_seq_len = initial_seq_len + pred_len
        full_stamp = torch.cat([x_stamp_tensor, y_stamp_tensor], dim=1)
        batch_size = x_token[0].size(0)

        generated_pre = x_token[0].new_empty(batch_size, pred_len)
        generated_post = x_token[1].new_empty(batch_size, pred_len)
        pre_buffer = x_token[0].new_zeros(batch_size, predictor.max_context)
        post_buffer = x_token[1].new_zeros(batch_size, predictor.max_context)
        buffer_len = min(initial_seq_len, predictor.max_context)
        if buffer_len:
            start_index = max(0, initial_seq_len - predictor.max_context)
            pre_buffer[:, :buffer_len] = x_token[0][
                :, start_index : start_index + buffer_len
            ]
            post_buffer[:, :buffer_len] = x_token[1][
                :, start_index : start_index + buffer_len
            ]

        for index in range(pred_len):
            current_seq_len = initial_seq_len + index
            window_len = min(current_seq_len, predictor.max_context)
            if current_seq_len <= predictor.max_context:
                input_tokens = [
                    pre_buffer[:, :window_len],
                    post_buffer[:, :window_len],
                ]
            else:
                input_tokens = [pre_buffer, post_buffer]

            context_end = current_seq_len
            context_start = max(0, context_end - predictor.max_context)
            current_stamp = full_stamp[
                :, context_start:context_end, :
            ].contiguous()

            s1_logits, context = predictor.model.decode_s1(
                input_tokens[0],
                input_tokens[1],
                current_stamp,
            )
            sample_pre = sample_from_logits(
                s1_logits[:, -1, :],
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                sample_logits=True,
            )
            s2_logits = predictor.model.decode_s2(context, sample_pre)
            sample_post = sample_from_logits(
                s2_logits[:, -1, :],
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                sample_logits=True,
            )

            generated_pre[:, index] = sample_pre.squeeze(-1)
            generated_post[:, index] = sample_post.squeeze(-1)
            if current_seq_len < predictor.max_context:
                pre_buffer[:, current_seq_len] = sample_pre.squeeze(-1)
                post_buffer[:, current_seq_len] = sample_post.squeeze(-1)
            else:
                pre_buffer.copy_(torch.roll(pre_buffer, shifts=-1, dims=1))
                post_buffer.copy_(torch.roll(post_buffer, shifts=-1, dims=1))
                pre_buffer[:, -1] = sample_pre.squeeze(-1)
                post_buffer[:, -1] = sample_post.squeeze(-1)

        full_pre = torch.cat([x_token[0], generated_pre], dim=1)
        full_post = torch.cat([x_token[1], generated_post], dim=1)
        context_start = max(0, total_seq_len - predictor.max_context)
        decoded = predictor.tokenizer.decode(
            [
                full_pre[:, context_start:total_seq_len].contiguous(),
                full_post[:, context_start:total_seq_len].contiguous(),
            ],
            half=True,
        )
        decoded = decoded.reshape(
            -1,
            sample_count,
            decoded.size(1),
            decoded.size(2),
        )
        return decoded[:, :, -pred_len:, :].cpu().numpy()[0]


def _predict_paths(
    engine: Any,
    frame: pd.DataFrame,
    x_timestamp: pd.Series,
    y_timestamp: pd.Series,
    params: ForecastParameters,
    path_count: int,
) -> list[pd.DataFrame]:
    columns = ["open", "high", "low", "close", "volume", "amount"]
    values = frame[columns].to_numpy(dtype=np.float32)
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    normalized = np.clip(
        (values - mean) / (std + 1e-5),
        -engine.predictor.clip,
        engine.predictor.clip,
    )[None, :, :]
    x_stamp = _time_features(x_timestamp)[None, :, :]
    y_stamp = _time_features(y_timestamp)[None, :, :]

    batches: list[np.ndarray] = []
    remaining = path_count
    with engine._model_lock:
        while remaining:
            count = min(_PATH_BATCH_SIZE, remaining)
            batches.append(
                _autoregressive_samples(
                    engine.predictor,
                    normalized,
                    x_stamp,
                    y_stamp,
                    pred_len=params.pred_len,
                    temperature=params.temperature,
                    top_k=params.top_k,
                    top_p=params.top_p,
                    sample_count=count,
                )
            )
            remaining -= count

    raw_paths = np.concatenate(batches, axis=0)
    raw_paths = raw_paths * (std + 1e-5) + mean
    paths: list[pd.DataFrame] = []
    for values_for_path in raw_paths:
        path = pd.DataFrame(
            values_for_path,
            columns=columns,
            index=pd.to_datetime(y_timestamp, utc=True),
        )
        path.index.name = "timestamp"
        paths.append(_enforce_constraints(path).reset_index())
    return paths


def _enforce_constraints(frame: pd.DataFrame) -> pd.DataFrame:
    clean = frame.copy()
    clean["high"] = clean[["high", "open", "close"]].max(axis=1)
    clean["low"] = clean[["low", "open", "close"]].min(axis=1)
    clean["volume"] = clean["volume"].clip(lower=0.0)
    clean["amount"] = clean["amount"].clip(lower=0.0)
    return clean


def _market_context(frame: pd.DataFrame) -> dict[str, Any]:
    data = frame.copy()
    for column in ("open", "high", "low", "close"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close"])
    if len(data) < 8:
        return {
            "regime": "unknown",
            "trend": {"direction": "unknown", "strength_pct": 0.0},
            "range": {"state": "unknown", "position_pct": None, "width_pct": None},
            "volatility": {"state": "unknown", "atr_pct": None, "relative_pct": None},
            "breakout": {"active": False, "score_pct": 0.0, "direction": "unknown"},
        }

    close = data["close"]
    price = max(abs(float(close.iloc[-1])), 1e-12)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.tail(min(14, len(true_range))).mean())
    atr_pct = atr / price * 100
    baseline_atr = float(true_range.tail(min(60, len(true_range))).median())
    relative_volatility = atr / max(baseline_atr, 1e-12) * 100

    ema20 = close.ewm(span=min(20, len(close)), adjust=False).mean()
    ema50 = close.ewm(span=min(50, len(close)), adjust=False).mean()
    slope_window = min(8, len(close) - 1)
    slope = float(ema20.iloc[-1] - ema20.iloc[-1 - slope_window])
    gap = float(ema20.iloc[-1] - ema50.iloc[-1])
    trend_value = gap + slope * 0.35
    threshold = max(atr * 0.12, price * 0.00002)
    trend_direction = _direction(trend_value, threshold)
    trend_strength = _clamp(
        abs(gap) / max(atr, 1e-12) * 45
        + abs(slope) / max(atr, 1e-12) * 20,
        0,
        100,
    )

    range_frame = data.tail(min(20, len(data)))
    range_low = float(range_frame["low"].min())
    range_high = float(range_frame["high"].max())
    range_width = max(range_high - range_low, 1e-12)
    range_position = _clamp(
        (float(close.iloc[-1]) - range_low) / range_width * 100,
        0,
        100,
    )
    range_width_pct = range_width / price * 100

    prior = data.iloc[:-1].tail(min(20, len(data) - 1))
    prior_high = float(prior["high"].max()) if not prior.empty else range_high
    prior_low = float(prior["low"].min()) if not prior.empty else range_low
    current = data.iloc[-1]
    candle_range = max(float(current["high"] - current["low"]), 1e-12)
    close_location = (float(current["close"] - current["low"])) / candle_range
    momentum = float(close.diff().tail(min(4, len(close) - 1)).sum())
    momentum_atr = momentum / max(atr, 1e-12)

    bullish_break = float(current["close"]) >= prior_high or (
        range_position >= 94 and momentum_atr > 0.45
    )
    bearish_break = float(current["close"]) <= prior_low or (
        range_position <= 6 and momentum_atr < -0.45
    )
    breakout_direction = (
        "bullish" if bullish_break else "bearish" if bearish_break else "sideways"
    )
    breakout_score = 0.0
    if bullish_break or bearish_break:
        directional_location = close_location if bullish_break else 1 - close_location
        breakout_score = _clamp(
            35
            + min(abs(momentum_atr), 2.0) * 20
            + directional_location * 20
            + max(0.0, relative_volatility - 100) * 0.08,
            0,
            100,
        )

    if breakout_score >= 60 and breakout_direction in {"bullish", "bearish"}:
        regime = f"{breakout_direction}_breakout"
    elif trend_strength >= 45 and trend_direction in {"bullish", "bearish"}:
        regime = f"{trend_direction}_trend"
    elif relative_volatility >= 145:
        regime = "volatile_range"
    else:
        regime = "range"

    if range_position >= 92:
        range_state = "testing upper boundary"
    elif range_position <= 8:
        range_state = "testing lower boundary"
    elif trend_strength < 28:
        range_state = "range-bound"
    else:
        range_state = "inside range"

    volatility_state = (
        "high"
        if relative_volatility >= 145
        else "low"
        if relative_volatility <= 70
        else "normal"
    )
    effective_direction = (
        breakout_direction
        if breakout_score >= 60
        else trend_direction
    )
    effective_strength = max(trend_strength, breakout_score)

    return {
        "regime": regime,
        "trend": {
            "direction": effective_direction,
            "strength_pct": round(effective_strength, 1),
            "ema_direction": trend_direction,
            "ema_strength_pct": round(trend_strength, 1),
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
            "atr": atr,
            "atr_pct": round(atr_pct, 4),
            "relative_pct": round(relative_volatility, 1),
        },
        "breakout": {
            "active": breakout_score >= 60,
            "score_pct": round(breakout_score, 1),
            "direction": breakout_direction,
            "momentum_atr": round(momentum_atr, 3),
            "close_location_pct": round(close_location * 100, 1),
            "proxy_only": True,
        },
    }


def _aggregate_paths(
    paths: Sequence[pd.DataFrame],
    quantile: float,
) -> pd.DataFrame:
    if not paths:
        raise ValueError("At least one forecast path is required.")
    length = min(len(path) for path in paths)
    columns = ("open", "high", "low", "close", "volume", "amount")
    rows: list[dict[str, Any]] = []
    for index in range(length):
        row = {
            "timestamp": pd.Timestamp(paths[0].iloc[index]["timestamp"]),
        }
        for column in columns:
            row[column] = _percentile(
                [float(path.iloc[index][column]) for path in paths],
                quantile,
            )
        rows.append(row)
    return _enforce_constraints(pd.DataFrame(rows))


def _path_direction(
    path: pd.DataFrame,
    base: float,
    horizon: int,
    threshold_pct: float,
) -> str:
    target = float(path.iloc[min(horizon, len(path)) - 1]["close"])
    move_pct = (target - base) / max(abs(base), 1e-12) * 100
    return _direction(move_pct, threshold_pct)


def _continuation_fallback(
    history: pd.DataFrame,
    timestamps: pd.Series,
    context: dict[str, Any],
    symbol: str,
) -> pd.DataFrame:
    direction = context["trend"]["direction"]
    sign = 1.0 if direction == "bullish" else -1.0
    close = history["close"].astype(float)
    base = float(close.iloc[-1])
    atr = float(context["volatility"].get("atr") or 0.0)
    recent_step = float(close.diff().tail(min(6, len(close) - 1)).median())
    step = sign * max(abs(recent_step), atr * 0.035)
    step = sign * min(abs(step), max(atr * 0.25, abs(base) * 0.0002))
    typical_range = float(
        (history["high"].astype(float) - history["low"].astype(float))
        .tail(min(14, len(history)))
        .median()
    )
    typical_range = max(typical_range, atr * 0.6, abs(base) * 0.0001)

    rows: list[dict[str, Any]] = []
    previous = base
    median_volume = float(history["volume"].tail(min(20, len(history))).median())
    for index, timestamp in enumerate(pd.to_datetime(timestamps, utc=True)):
        decay = math.exp(-index / max(len(timestamps) * 1.5, 1))
        projected_close = previous + step * decay
        projected_open = previous
        high = max(projected_open, projected_close) + typical_range * 0.28
        low = min(projected_open, projected_close) - typical_range * 0.28
        volume = 0.0 if symbol in INDEX_SYMBOLS else median_volume
        rows.append(
            {
                "timestamp": timestamp,
                "open": projected_open,
                "high": high,
                "low": low,
                "close": projected_close,
                "volume": volume,
                "amount": 0.0 if symbol in INDEX_SYMBOLS else volume * projected_close,
            }
        )
        previous = projected_close
    return _enforce_constraints(pd.DataFrame(rows))


def _ensemble(
    paths: list[pd.DataFrame],
    *,
    base: float,
    timeframe: str,
    context: dict[str, Any],
    history: pd.DataFrame,
    future_timestamps: pd.Series,
    symbol: str,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    horizon = min(ONE_HOUR_HORIZONS.get(timeframe, len(paths[0])), len(paths[0]))
    threshold_pct = max(
        float(context["volatility"].get("atr_pct") or 0.0) * 0.10,
        0.002,
    )
    directions = [
        _path_direction(path, base, horizon, threshold_pct)
        for path in paths
    ]
    counts = {name: directions.count(name) for name in ("bullish", "bearish", "sideways")}
    vote_direction = max(counts, key=counts.get)
    vote_pct = counts[vote_direction] / len(paths) * 100

    selected = list(paths)
    gate = {
        "applied": False,
        "status": "passed",
        "reason": None,
        "trend_direction": context["trend"]["direction"],
        "trend_strength_pct": context["trend"]["strength_pct"],
        "raw_vote_direction": vote_direction,
        "raw_vote_pct": round(vote_pct, 1),
        "selected_paths": len(selected),
        "total_paths": len(paths),
        "trade_allowed": True,
    }

    trend_direction = context["trend"]["direction"]
    trend_strength = float(context["trend"]["strength_pct"])
    strong_regime = (
        trend_direction in {"bullish", "bearish"}
        and trend_strength >= 55
        and (
            context["breakout"].get("active")
            or context["regime"].endswith("_trend")
        )
    )
    opposite = (
        vote_direction in {"bullish", "bearish"}
        and vote_direction != trend_direction
    )
    if strong_regime:
        aligned = [
            path
            for path, direction in zip(paths, directions)
            if direction == trend_direction
        ]
        if opposite and len(aligned) >= max(2, math.ceil(len(paths) * 0.20)):
            selected = aligned
            gate.update(
                {
                    "applied": True,
                    "status": "countertrend_paths_filtered",
                    "reason": "Path vote opposed a strong trend/breakout regime.",
                    "selected_paths": len(selected),
                    "trade_allowed": len(aligned) / len(paths) >= 0.35,
                }
            )
        elif opposite:
            selected = []
            gate.update(
                {
                    "applied": True,
                    "status": "countertrend_forecast_blocked",
                    "reason": "No reliable sampled path agreed with the strong regime.",
                    "selected_paths": 0,
                    "trade_allowed": False,
                    "fallback": "momentum_continuation_proxy",
                }
            )

    if selected:
        projection = _aggregate_paths(selected, 0.5)
    else:
        projection = _continuation_fallback(
            history,
            future_timestamps,
            context,
            symbol,
        )

    selected_for_bands = selected or paths
    p25 = _aggregate_paths(selected_for_bands, 0.25)
    p75 = _aggregate_paths(selected_for_bands, 0.75)
    summary = {
        "paths": len(paths),
        "selected_paths": len(selected),
        "median": _records(_aggregate_paths(selected_for_bands, 0.5)),
        "p25": _records(p25),
        "p75": _records(p75),
        "p10": _records(_aggregate_paths(selected_for_bands, 0.10)),
        "p90": _records(_aggregate_paths(selected_for_bands, 0.90)),
        "directional_vote": {
            "direction": vote_direction,
            "agreement_pct": round(vote_pct, 1),
            "counts": counts,
            "horizon_candles": horizon,
            "target_window": "1h",
        },
        "bullish_probability": [
            sum(
                float(path.iloc[index]["close"]) >= base
                for path in paths
            )
            / len(paths)
            * 100
            for index in range(min(len(path) for path in paths))
        ],
        "mean_iqr_width": statistics.fmean(
            max(0.0, float(p75.iloc[index]["close"]) - float(p25.iloc[index]["close"]))
            for index in range(min(len(path) for path in selected_for_bands))
        ),
    }
    return projection, summary, gate


def _raw_forecasts(
    store: PlatformStore,
    symbol: str,
    timeframe: str,
    limit: int,
) -> list[dict[str, Any]]:
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM forecasts WHERE symbol=? AND timeframe=? "
            "ORDER BY generated_at DESC LIMIT ?",
            (normalize_symbol(symbol), timeframe, max(1, min(limit, 500))),
        ).fetchall()
    return [PlatformStore._forecast_row(row) for row in rows]


def _raw_forecast(
    store: PlatformStore,
    forecast_id: str,
) -> dict[str, Any] | None:
    with store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM forecasts WHERE id=?",
            (forecast_id,),
        ).fetchone()
    return PlatformStore._forecast_row(row) if row else None


def _calibration(
    store: PlatformStore,
    *,
    symbol: str,
    timeframe: str,
    horizon: int,
    regime: str,
) -> dict[str, Any]:
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT s.forecast_id,s.direction_correct,s.close_error_pct,
                   f.projection_json,f.revision_json
            FROM forecast_scores s
            JOIN forecasts f ON f.id=s.forecast_id
            WHERE f.symbol=? AND f.timeframe=? AND s.horizon=?
            ORDER BY s.scored_at DESC
            LIMIT 1000
            """,
            (normalize_symbol(symbol), timeframe, int(horizon)),
        ).fetchall()

    filtered: list[dict[str, Any]] = []
    for row in rows:
        revision = json.loads(row["revision_json"]) if row["revision_json"] else {}
        context = revision.get("market_context") or {}
        if context.get("regime") != regime:
            continue
        projection = json.loads(row["projection_json"])
        intrabar = revision.get("intrabar") or {}
        base = intrabar.get("close")
        if base is None or not projection:
            continue
        target = float(projection[min(horizon, len(projection)) - 1]["close"])
        predicted_move_pct = abs(
            (target - float(base)) / max(abs(float(base)), 1e-12) * 100
        )
        tolerance = max(predicted_move_pct * 0.50, 0.05)
        filtered.append(
            {
                "forecast_id": row["forecast_id"],
                "direction_correct": int(row["direction_correct"]),
                "distance_hit": float(row["close_error_pct"]) <= tolerance,
            }
        )

    independent = {row["forecast_id"]: row for row in filtered}
    samples = list(independent.values())
    count = len(samples)
    payload: dict[str, Any] = {
        "available": count >= MIN_CALIBRATION_FORECASTS,
        "calibrated": count >= MIN_CALIBRATION_FORECASTS,
        "independent_forecasts": count,
        "required_forecasts": MIN_CALIBRATION_FORECASTS,
        "symbol": normalize_symbol(symbol),
        "timeframe": timeframe,
        "horizon": int(horizon),
        "regime": regime,
    }
    if count < MIN_CALIBRATION_FORECASTS:
        return payload

    direction_accuracy = statistics.fmean(
        row["direction_correct"] for row in samples
    ) * 100
    distance_accuracy = statistics.fmean(
        int(row["distance_hit"]) for row in samples
    ) * 100
    score = direction_accuracy * 0.70 + distance_accuracy * 0.30
    payload.update(
        {
            "score_pct": round(score, 1),
            "grade": "high" if score >= 65 else "medium" if score >= 55 else "low",
            "components": {
                "direction_accuracy_pct": round(direction_accuracy, 1),
                "distance_accuracy_pct": round(distance_accuracy, 1),
            },
        }
    )
    return payload


def _attach_v2_metadata(
    store: PlatformStore,
    item: dict[str, Any],
) -> dict[str, Any]:
    enhanced = dict(item)
    revision = dict(enhanced.get("revision") or {})
    context = revision.get("market_context") or {}
    horizon = int(
        revision.get("path_ensemble", {})
        .get("directional_vote", {})
        .get(
            "horizon_candles",
            ONE_HOUR_HORIZONS.get(enhanced["timeframe"], 1),
        )
    )
    confidence = _calibration(
        store,
        symbol=enhanced["symbol"],
        timeframe=enhanced["timeframe"],
        horizon=horizon,
        regime=context.get("regime", "unknown"),
    )
    confidence["sample_count"] = int(
        (enhanced.get("parameters") or {}).get(
            "sample_count",
            NORMAL_SAMPLE_COUNT,
        )
    )
    confidence["paths"] = int(
        revision.get("path_ensemble", {}).get(
            "paths",
            confidence["sample_count"],
        )
    )
    enhanced["confidence"] = confidence
    revision["model_confidence"] = confidence
    enhanced["revision"] = revision
    if enhanced.get("uncertainty"):
        uncertainty = dict(enhanced["uncertainty"])
        uncertainty["confidence"] = confidence
        enhanced["uncertainty"] = uncertainty
    return enhanced


def forecasts_v2(
    self: PlatformStore,
    symbol: str,
    timeframe: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    return [
        _attach_v2_metadata(self, item)
        for item in _raw_forecasts(self, symbol, timeframe, limit)
    ]


def forecast_v2(
    self: PlatformStore,
    forecast_id: str,
) -> dict[str, Any] | None:
    item = _raw_forecast(self, forecast_id)
    return _attach_v2_metadata(self, item) if item else None


def _matching_cache(
    platform: ForecastPlatform,
    *,
    symbol: str,
    timeframe: str,
    completed_timestamp: str,
    intrabar_signature: str,
    params: ForecastParameters,
    advanced: bool,
    path_count: int,
) -> dict[str, Any] | None:
    for item in _raw_forecasts(platform.store, symbol, timeframe, 30):
        parameters = item.get("parameters") or {}
        revision = item.get("revision") or {}
        ensemble = revision.get("path_ensemble") or {}
        try:
            same_completed = _timestamp(item.get("input_last_timestamp")) == completed_timestamp
        except Exception:
            same_completed = False
        if (
            same_completed
            and revision.get("intrabar", {}).get("signature") == intrabar_signature
            and item.get("model_id") == platform.engine.settings.model_id
            and int(parameters.get("lookback", -1)) == int(params.lookback)
            and int(parameters.get("pred_len", -1)) == int(params.pred_len)
            and int(parameters.get("sample_count", -1)) == int(path_count)
            and bool(item.get("uncertainty")) == bool(advanced)
            and int(ensemble.get("paths", -1)) == int(path_count)
        ):
            return _attach_v2_metadata(platform.store, item)
    return None


def _intrabar_signature(row: dict[str, Any]) -> str:
    # One forecast per forming candle. The first tab snapshots the candle and all
    # other tabs reuse that same forecast until a new candle begins.
    return _timestamp(row["timestamp"])


def generate_v2(
    self: ForecastPlatform,
    params: ForecastParameters,
    *,
    advanced: bool = False,
    paths: int | None = None,
) -> dict[str, Any]:
    symbol = normalize_symbol(params.symbol)
    path_count = (
        max(ADVANCED_PATH_COUNT, int(paths or 0))
        if advanced
        else max(NORMAL_SAMPLE_COUNT, int(params.sample_count))
    )
    effective = replace(params, symbol=symbol, sample_count=path_count)
    lookback = min(effective.lookback, self.engine.settings.max_context)

    completed = self.engine.candles(symbol, effective.timeframe, lookback)
    current_frame = self.engine.provider.get_current_candle(
        symbol,
        effective.timeframe,
    )
    if current_frame is None or current_frame.empty:
        current_frame = completed.tail(1).copy()
    current = current_frame.tail(1).reset_index(drop=True)
    current_row = _records(current)[0]
    completed_timestamp = _timestamp(completed["timestamp"].iloc[-1])
    intrabar_signature = _intrabar_signature(current_row)

    identity = (
        symbol,
        effective.timeframe,
        completed_timestamp,
        intrabar_signature,
        self.engine.settings.model_id,
        self.engine.settings.tokenizer_id,
        lookback,
        effective.pred_len,
        path_count,
        effective.temperature,
        effective.top_k,
        effective.top_p,
        advanced,
    )
    with _GENERATION_GUARD:
        lock = _GENERATION_LOCKS.setdefault(identity, threading.Lock())

    with lock:
        cached = _matching_cache(
            self,
            symbol=symbol,
            timeframe=effective.timeframe,
            completed_timestamp=completed_timestamp,
            intrabar_signature=intrabar_signature,
            params=effective,
            advanced=advanced,
            path_count=path_count,
        )
        if cached:
            return {
                **cached,
                "reused": True,
                "advanced": advanced,
            }

        model_history = completed.copy()
        current_timestamp = pd.Timestamp(current["timestamp"].iloc[-1])
        if current_timestamp > pd.Timestamp(completed["timestamp"].iloc[-1]):
            model_history = pd.concat(
                [model_history, current],
                ignore_index=True,
            ).tail(lookback)

        model_input = model_history[
            ["open", "high", "low", "close", "volume", "amount"]
        ].copy()
        feature_mode = "ohlcv"
        if symbol in INDEX_SYMBOLS:
            model_input["volume"] = 0.0
            model_input["amount"] = 0.0
            feature_mode = "price_only"

        x_timestamp = pd.Series(
            pd.to_datetime(model_history["timestamp"], utc=True),
            name="timestamp",
        )
        future_timestamps = pd.Series(
            self.engine.provider.future_timestamps(
                symbol=symbol,
                timeframe=effective.timeframe,
                last_timestamp=model_history["timestamp"].iloc[-1],
                periods=effective.pred_len,
            ),
            name="timestamp",
        )
        inference_started = pd.Timestamp.now(tz="UTC")
        sampled_paths = _predict_paths(
            self.engine,
            model_input,
            x_timestamp,
            future_timestamps,
            effective,
            path_count,
        )
        context = _market_context(model_history)
        base = float(model_history["close"].iloc[-1])
        projection, ensemble, gate = _ensemble(
            sampled_paths,
            base=base,
            timeframe=effective.timeframe,
            context=context,
            history=model_history,
            future_timestamps=future_timestamps,
            symbol=symbol,
        )
        elapsed = (
            pd.Timestamp.now(tz="UTC") - inference_started
        ).total_seconds() * 1000

        previous = _raw_forecasts(
            self.store,
            symbol,
            effective.timeframe,
            1,
        )
        if previous:
            revision = revision_metrics(
                previous[0].get("projection") or [],
                _records(projection),
            )
        else:
            vote = ensemble["directional_vote"]
            revision = {
                "available": True,
                "severity": "baseline",
                "severity_score": 0.0,
                "direction_previous": vote["direction"],
                "direction_active": vote["direction"],
                "direction_flip": False,
                "move_previous_pct": 0.0,
                "move_active_pct": 0.0,
                "magnitude_change_pct_points": 0.0,
                "path_similarity_pct": vote["agreement_pct"],
                "timing_shift_candles": 0,
                "volatility_change_pct": 0.0,
                "stability_score": vote["agreement_pct"],
                "candle_consensus_pct": vote["agreement_pct"],
            }

        revision = dict(revision)
        revision.update(
            {
                "market_context": context,
                "regime_gate": gate,
                "intrabar": {
                    **current_row,
                    "signature": intrabar_signature,
                    "used": current_timestamp
                    > pd.Timestamp(completed["timestamp"].iloc[-1]),
                },
                "feature_mode": feature_mode,
                "path_ensemble": {
                    **ensemble,
                    "all_paths": [
                        _records(path)
                        for path in sampled_paths
                    ],
                },
            }
        )
        uncertainty = (
            {
                **ensemble,
                "all_paths": [
                    _records(path)
                    for path in sampled_paths
                ],
                "gate": gate,
            }
            if advanced
            else None
        )
        parameters = {
            **asdict(effective),
            "sample_count": path_count,
            "feature_mode": feature_mode,
            "intrabar": True,
            "aggregation": "median_directional_vote",
            "regime_gate": True,
        }

        forecast_id = self.store.save_forecast(
            symbol=symbol,
            timeframe=effective.timeframe,
            model_id=self.engine.settings.model_id,
            tokenizer_id=self.engine.settings.tokenizer_id,
            parameters=parameters,
            history=completed,
            projection=projection,
            source=self.engine.provider.name,
            inference_ms=elapsed,
            uncertainty=uncertainty,
            revision=revision,
        )

        horizon = ensemble["directional_vote"]["horizon_candles"]
        confidence = _calibration(
            self.store,
            symbol=symbol,
            timeframe=effective.timeframe,
            horizon=horizon,
            regime=context["regime"],
        )
        confidence.update(
            {
                "sample_count": path_count,
                "paths": path_count,
                "path_vote": ensemble["directional_vote"],
            }
        )
        revision["model_confidence"] = confidence
        with self.store._lock, self.store.connection() as connection:
            connection.execute(
                "UPDATE forecasts SET revision_json=? WHERE id=?",
                (
                    json.dumps(revision, separators=(",", ":"), default=str),
                    forecast_id,
                ),
            )

        return {
            "id": forecast_id,
            "symbol": symbol,
            "timeframe": effective.timeframe,
            "generated_at": utc_now_iso(),
            "input_last_timestamp": completed_timestamp,
            "history": _records(completed),
            "projection": _records(projection),
            "uncertainty": uncertainty,
            "revision": revision,
            "confidence": confidence,
            "parameters": parameters,
            "advanced": advanced,
            "inference_ms": elapsed,
            "intrabar_input": current_row,
            "feature_mode": feature_mode,
            "regime_gate": gate,
        }


def score_realized_v2(
    self: PlatformStore,
    symbol: str,
    timeframe: str,
    actual: pd.DataFrame,
) -> int:
    canonical = normalize_symbol(symbol)
    actual_rows = {
        _timestamp(row["timestamp"]): row
        for row in actual.to_dict(orient="records")
    }
    if not actual_rows:
        return 0

    with self.connection() as connection:
        forecasts = connection.execute(
            "SELECT id,history_json,projection_json,revision_json "
            "FROM forecasts WHERE symbol=? AND timeframe=?",
            (canonical, timeframe),
        ).fetchall()
        existing = {
            (row["forecast_id"], int(row["horizon"]))
            for row in connection.execute(
                "SELECT forecast_id,horizon FROM forecast_scores"
            ).fetchall()
        }

    inserted = 0
    with self._lock, self.connection() as connection:
        for forecast in forecasts:
            history = json.loads(forecast["history_json"])
            projection = json.loads(forecast["projection_json"])
            revision = (
                json.loads(forecast["revision_json"])
                if forecast["revision_json"]
                else {}
            )
            base_close = float(
                (revision.get("intrabar") or {}).get(
                    "close",
                    history[-1]["close"],
                )
            )
            for index, predicted in enumerate(projection, start=1):
                key = (forecast["id"], index)
                observed = actual_rows.get(_timestamp(predicted["timestamp"]))
                if key in existing or observed is None:
                    continue
                predicted_close = float(predicted["close"])
                actual_close = float(observed["close"])
                predicted_direction = _direction(predicted_close - base_close)
                actual_direction = _direction(actual_close - base_close)
                volume_actual = float(observed.get("volume") or 0)
                volume_predicted = float(predicted.get("volume") or 0)
                volume_error = (
                    abs(volume_predicted - volume_actual)
                    / volume_actual
                    * 100
                    if volume_actual
                    else None
                )
                connection.execute(
                    """
                    INSERT INTO forecast_scores(
                        forecast_id,horizon,target_timestamp,scored_at,
                        actual_json,close_error,close_error_pct,
                        direction_correct,range_hit,high_error,low_error,
                        volume_error_pct
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        forecast["id"],
                        index,
                        _timestamp(predicted["timestamp"]),
                        utc_now_iso(),
                        json.dumps(observed, separators=(",", ":"), default=str),
                        abs(predicted_close - actual_close),
                        abs(predicted_close - actual_close)
                        / max(abs(actual_close), 1e-12)
                        * 100,
                        int(predicted_direction == actual_direction),
                        int(
                            float(predicted["low"])
                            <= actual_close
                            <= float(predicted["high"])
                        ),
                        abs(float(predicted["high"]) - float(observed["high"])),
                        abs(float(predicted["low"]) - float(observed["low"])),
                        volume_error,
                    ),
                )
                inserted += 1
    return inserted


def consensus_v2(
    self: ForecastPlatform,
    symbol: str,
    selected_timeframe: str,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)

    # Keep the three one-hour comparison inputs on the same current-candle
    # generation. Auxiliary timeframes use normal mode to avoid 14-path work.
    for timeframe in ALIGNED_TIMEFRAMES:
        try:
            completed = self.engine.candles(canonical, timeframe, 2)
            current = self.engine.provider.get_current_candle(canonical, timeframe)
            current_timestamp = (
                _timestamp(current["timestamp"].iloc[-1])
                if current is not None and not current.empty
                else _timestamp(completed["timestamp"].iloc[-1])
            )
            latest = _raw_forecasts(self.store, canonical, timeframe, 1)
            revision = (latest[0].get("revision") or {}) if latest else {}
            fresh = (
                bool(latest)
                and _timestamp(latest[0].get("input_last_timestamp"))
                == _timestamp(completed["timestamp"].iloc[-1])
                and (revision.get("intrabar") or {}).get("signature")
                == current_timestamp
            )
            if not fresh:
                self.generate(
                    ForecastParameters(
                        symbol=canonical,
                        timeframe=timeframe,
                        lookback=self.engine.settings.default_lookback,
                        pred_len=max(self.engine.settings.default_pred_len, 12),
                        sample_count=NORMAL_SAMPLE_COUNT,
                    ),
                    advanced=False,
                )
        except Exception:
            # A missing auxiliary timeframe is represented as unknown below; it
            # must not break the selected chart.
            pass

    readings: list[dict[str, Any]] = []
    histories: dict[str, list[dict[str, Any]]] = {}

    for timeframe in ALIGNED_TIMEFRAMES:
        latest = self.store.forecasts(canonical, timeframe, 1)
        if not latest:
            readings.append(
                {
                    "timeframe": timeframe,
                    "direction": "unknown",
                    "move_pct": None,
                    "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
                }
            )
            continue
        item = latest[0]
        history = item.get("history") or []
        projection = item.get("projection") or []
        histories[timeframe] = history
        if not history or not projection:
            readings.append(
                {
                    "timeframe": timeframe,
                    "direction": "unknown",
                    "move_pct": None,
                    "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
                }
            )
            continue
        intrabar = (item.get("revision") or {}).get("intrabar") or {}
        base = float(intrabar.get("close", history[-1]["close"]))
        horizon = min(ONE_HOUR_HORIZONS[timeframe], len(projection))
        target = float(projection[horizon - 1]["close"])
        move_pct = (target - base) / max(abs(base), 1e-12) * 100
        context = (item.get("revision") or {}).get("market_context") or {}
        threshold = max(
            float(
                (context.get("volatility") or {}).get("atr_pct") or 0.0
            )
            * 0.10,
            0.002,
        )
        readings.append(
            {
                "timeframe": timeframe,
                "direction": _direction(move_pct, threshold),
                "move_pct": round(move_pct, 4),
                "horizon_candles": horizon,
                "target_window": "1h",
                "forecast_id": item["id"],
                "confidence": item.get("confidence"),
                "gate": (item.get("revision") or {}).get("regime_gate"),
            }
        )

    directions = [row["direction"] for row in readings]
    known = [direction for direction in directions if direction != "unknown"]
    if known:
        consensus = max(
            ("bullish", "bearish", "sideways"),
            key=lambda value: known.count(value),
        )
        agreement_pct = known.count(consensus) / len(ALIGNED_TIMEFRAMES) * 100
    else:
        consensus = "unknown"
        agreement_pct = 0.0

    complete = len(known) == len(ALIGNED_TIMEFRAMES)
    contradiction = "bullish" in known and "bearish" in known
    aligned = (
        complete
        and not contradiction
        and consensus in {"bullish", "bearish"}
        and agreement_pct == 100.0
    )
    trade_allowed = aligned and all(
        (row.get("gate") or {}).get("trade_allowed", True)
        for row in readings
    )

    context_item = self.store.forecasts(canonical, "15m", 1)
    market_context = (
        (context_item[0].get("revision") or {}).get("market_context")
        if context_item
        else {}
    )
    return {
        "selected": selected_timeframe,
        "target_window": "1h",
        "readings": readings,
        "agreement_pct": round(agreement_pct, 1),
        "consensus": consensus if not contradiction else "conflict",
        "aligned": aligned,
        "contradiction": contradiction,
        "complete": complete,
        "trade_bias": consensus if trade_allowed else "no_trade",
        "trade_allowed": trade_allowed,
        "alignment_status": (
            "aligned"
            if trade_allowed
            else "conflict"
            if contradiction
            else "incomplete"
            if not complete
            else "mixed"
        ),
        "market_context": market_context or {},
    }


PlatformStore.forecasts = forecasts_v2  # type: ignore[method-assign]
PlatformStore.forecast = forecast_v2  # type: ignore[method-assign]
PlatformStore.score_realized = score_realized_v2  # type: ignore[method-assign]
ForecastPlatform.generate = generate_v2  # type: ignore[method-assign]
ForecastPlatform.consensus = consensus_v2  # type: ignore[method-assign]


# Existing cached singleton instances use the patched class methods immediately.
get_platform()
