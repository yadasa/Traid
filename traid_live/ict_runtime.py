from __future__ import annotations

import contextvars
import json
import math
import statistics
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .forecast import ForecastParameters
from .market import normalize_symbol
from .platform import ForecastPlatform, PlatformStore
from . import intelligence_v2 as v2
from . import trajectory_integrity as trajectory
from .ict_context import ICT_VERSION, analyze_ict, zone_touched


RUNTIME_VERSION = "ict_ranked_sample_v1"
MIN_CONTEXT_CALIBRATION_FORECASTS = 30
ALIGNED_TIMEFRAMES = ("5m", "15m", "1h")
ONE_HOUR_HORIZONS = {"5m": 12, "15m": 4, "1h": 1}

_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "traid_ict_runtime_context",
    default=None,
)

_BASE_GENERATE = ForecastPlatform.generate
_BASE_CONSENSUS = ForecastPlatform.consensus
_BASE_STORE_FORECASTS = PlatformStore.forecasts
_BASE_STORE_FORECAST = PlatformStore.forecast
_BASE_MARKET_CONTEXT = v2._market_context
_BASE_ENSEMBLE = v2._ensemble
_BASE_MATCHING_CACHE = v2._matching_cache


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _direction(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "bullish"
    if value < -threshold:
        return "bearish"
    return "sideways"


def _runtime_events(platform: ForecastPlatform, symbol: str) -> list[dict[str, Any]]:
    del symbol
    now = pd.Timestamp.now(tz="UTC")
    try:
        return platform.store.events(
            start=(now - pd.Timedelta(hours=3)).isoformat(),
            end=(now + pd.Timedelta(hours=3)).isoformat(),
        )
    except Exception:
        return []


def _higher_timeframe_contexts(
    platform: ForecastPlatform,
    symbol: str,
    timeframe: str,
) -> dict[str, dict[str, Any]]:
    wanted = (
        ("15m", "1h")
        if timeframe == "5m"
        else ("1h",)
        if timeframe == "15m"
        else ()
    )
    output: dict[str, dict[str, Any]] = {}
    for higher in wanted:
        try:
            items = _BASE_STORE_FORECASTS(platform.store, symbol, higher, 1)
        except Exception:
            items = []
        if not items:
            continue
        revision = items[0].get("revision") or {}
        ict = revision.get("ict_context") or (
            (revision.get("market_context") or {}).get("ict")
        )
        if ict:
            output[higher] = ict
    return output


def market_context_with_ict(frame: pd.DataFrame) -> dict[str, Any]:
    context = dict(_BASE_MARKET_CONTEXT(frame))
    runtime = _RUNTIME_CONTEXT.get() or {}
    symbol = str(runtime.get("symbol") or "UNKNOWN")
    timeframe = str(runtime.get("timeframe") or "5m")
    ict = analyze_ict(
        frame,
        symbol=symbol,
        timeframe=timeframe,
        events=runtime.get("events") or [],
    )
    higher_timeframes = runtime.get("higher_timeframes") or {}
    if higher_timeframes:
        ict["higher_timeframes"] = higher_timeframes
        for higher in ("1h", "15m"):
            higher_bias = (
                (higher_timeframes.get(higher) or {})
                .get("structure", {})
                .get("bias")
            )
            if higher_bias in {"bullish", "bearish"}:
                ict["hierarchy_bias"] = higher_bias
                ict["hierarchy_source"] = higher
                break
    context["ict"] = ict
    context["ict_version"] = ICT_VERSION

    structure = ict.get("structure") or {}
    setup = ict.get("setup") or {}
    context["structure_bias"] = structure.get("bias", "sideways")
    context["setup_state"] = setup.get("state", "waiting")
    context["setup_quality_pct"] = setup.get("quality_pct", 0.0)
    if structure.get("bias") in {"bullish", "bearish"}:
        context["trend"] = dict(context.get("trend") or {})
        context["trend"]["ict_direction"] = structure["bias"]
        context["trend"]["ict_strength_pct"] = structure.get("strength_pct", 0.0)
    return context


def _path_direction(
    path: pd.DataFrame,
    *,
    base: float,
    horizon: int,
    threshold_pct: float,
) -> str:
    target = float(path.iloc[min(horizon, len(path)) - 1]["close"])
    move_pct = (target - base) / max(abs(base), 1e-12) * 100
    return _direction(move_pct, threshold_pct)


def _median_distances(paths: Sequence[pd.DataFrame]) -> list[float]:
    length = min(len(path) for path in paths)
    matrix = np.asarray(
        [path.iloc[:length]["close"].astype(float).to_numpy() for path in paths],
        dtype=float,
    )
    median = np.median(matrix, axis=0)
    scale = max(float(np.ptp(median)), abs(float(median[-1])) * 1e-6, 1e-9)
    raw = np.mean(np.abs(matrix - median), axis=1) / scale
    maximum = max(float(np.max(raw)), 1e-12)
    return [float(value / maximum) for value in raw]


def _liquidity_score(
    path: pd.DataFrame,
    *,
    base: float,
    draw: dict[str, Any] | None,
    bias: str,
) -> float:
    if not draw or bias not in {"bullish", "bearish"}:
        return 50.0
    target = float(draw.get("price") or base)
    end = float(path.iloc[-1]["close"])
    if bias == "bullish":
        denominator = max(target - base, abs(base) * 1e-9)
        progress = (end - base) / denominator
        reached = float(path["high"].max()) >= target
    else:
        denominator = max(base - target, abs(base) * 1e-9)
        progress = (base - end) / denominator
        reached = float(path["low"].min()) <= target
    score = _clamp(progress * 75 + (25 if reached else 0))
    if progress < -0.25:
        score *= 0.25
    return score


def _zone_score(path: pd.DataFrame, *, ict: dict[str, Any], bias: str) -> float:
    if bias not in {"bullish", "bearish"}:
        return 50.0
    fvg = ict.get("fair_value_gaps") or {}
    zone = fvg.get("nearest_bullish") if bias == "bullish" else fvg.get("nearest_bearish")
    order_block = ict.get("order_block")
    limit = max(1, len(path) // 3)
    fvg_touch = zone_touched(path, zone, limit)
    ob_touch = zone_touched(
        path,
        order_block if (order_block or {}).get("direction") == bias else None,
        limit,
    )
    endpoint = float(path.iloc[-1]["close"])
    start = float(path.iloc[0]["open"])
    directional = endpoint > start if bias == "bullish" else endpoint < start
    return _clamp(35 + (25 if fvg_touch else 0) + (20 if ob_touch else 0) + (20 if directional else 0))


def _dealing_range_score(path: pd.DataFrame, *, ict: dict[str, Any], bias: str) -> float:
    dealing = ict.get("dealing_range") or {}
    low = float(dealing.get("low") or 0)
    high = float(dealing.get("high") or 0)
    if high <= low or bias not in {"bullish", "bearish"}:
        return 50.0
    midpoint = (low + high) / 2
    early = path.head(max(1, len(path) // 3))
    if bias == "bullish":
        favorable_rebalance = float(early["low"].min()) <= midpoint
        closes_correctly = float(path.iloc[-1]["close"]) >= midpoint
    else:
        favorable_rebalance = float(early["high"].max()) >= midpoint
        closes_correctly = float(path.iloc[-1]["close"]) <= midpoint
    return 40 + (30 if favorable_rebalance else 0) + (30 if closes_correctly else 0)


def _displacement_score(path: pd.DataFrame, *, bias: str, atr: float) -> float:
    if bias not in {"bullish", "bearish"} or path.empty:
        return 50.0
    sample = path.head(min(3, len(path)))
    move = float(sample.iloc[-1]["close"] - sample.iloc[0]["open"])
    direction = _direction(move, max(atr * 0.08, 1e-12))
    magnitude = abs(move) / max(atr, 1e-12)
    if direction == bias:
        return _clamp(65 + min(magnitude, 2.0) * 17.5)
    if direction == "sideways":
        return 45.0
    return 10.0


def _plausibility_score(path: pd.DataFrame, *, base: float, atr: float, horizon: int) -> float:
    target = float(path.iloc[min(horizon, len(path)) - 1]["close"])
    move_atr = abs(target - base) / max(atr, 1e-12)
    expected = math.sqrt(max(horizon, 1))
    ratio = move_atr / max(expected, 1e-12)
    if 0.12 <= ratio <= 1.8:
        return 80.0
    if 0.05 <= ratio <= 2.8:
        return 58.0
    return 25.0


def _rank_paths(
    paths: list[pd.DataFrame],
    *,
    base: float,
    timeframe: str,
    context: dict[str, Any],
) -> tuple[int, list[dict[str, Any]], dict[str, int], str, float]:
    ict = context.get("ict") or {}
    model = ict.get("context_model") or {}
    setup = ict.get("setup") or {}
    structure = ict.get("structure") or {}
    bias = (
        ict.get("hierarchy_bias")
        if ict.get("hierarchy_bias") in {"bullish", "bearish"}
        else model.get("dominant")
        if model.get("dominant") in {"bullish", "bearish"}
        else setup.get("bias")
        if setup.get("bias") in {"bullish", "bearish"}
        else structure.get("bias")
    )
    if bias not in {"bullish", "bearish"}:
        bias = "sideways"

    horizon = min(ONE_HOUR_HORIZONS.get(timeframe, len(paths[0])), len(paths[0]))
    atr = float(ict.get("atr") or (context.get("volatility") or {}).get("atr") or 0.0)
    threshold_pct = max(float((context.get("volatility") or {}).get("atr_pct") or 0.0) * 0.10, 0.002)
    directions = [
        _path_direction(path, base=base, horizon=horizon, threshold_pct=threshold_pct)
        for path in paths
    ]
    counts = {direction: directions.count(direction) for direction in ("bullish", "bearish", "sideways")}
    vote_direction = max(counts, key=counts.get)
    vote_pct = counts[vote_direction] / len(paths) * 100
    medoid_distance = _median_distances(paths)
    draw = (ict.get("liquidity") or {}).get("draw")

    ranked: list[dict[str, Any]] = []
    for index, (path, direction) in enumerate(zip(paths, directions)):
        direction_support = counts[direction] / len(paths) * 100
        if bias in {"bullish", "bearish"}:
            structure_score = 100.0 if direction == bias else 55.0 if direction == "sideways" else 5.0
        else:
            structure_score = 70.0 if direction == vote_direction else 40.0

        components = {
            "path_support_pct": direction_support,
            "median_proximity_pct": (1 - medoid_distance[index]) * 100,
            "structure_alignment_pct": structure_score,
            "liquidity_objective_pct": _liquidity_score(path, base=base, draw=draw, bias=bias),
            "fvg_order_block_pct": _zone_score(path, ict=ict, bias=bias),
            "premium_discount_pct": _dealing_range_score(path, ict=ict, bias=bias),
            "displacement_pct": _displacement_score(path, bias=bias, atr=atr),
            "volatility_plausibility_pct": _plausibility_score(path, base=base, atr=atr, horizon=horizon),
        }
        total = (
            components["path_support_pct"] * 0.22
            + components["median_proximity_pct"] * 0.10
            + components["structure_alignment_pct"] * 0.20
            + components["liquidity_objective_pct"] * 0.15
            + components["fvg_order_block_pct"] * 0.10
            + components["premium_discount_pct"] * 0.08
            + components["displacement_pct"] * 0.08
            + components["volatility_plausibility_pct"] * 0.07
        )
        ranked.append(
            {
                "index": index,
                "direction": direction,
                "score_pct": round(_clamp(total), 2),
                "components": {key: round(float(value), 1) for key, value in components.items()},
            }
        )

    minimum_support = max(2, math.ceil(len(paths) * 0.20))
    preferred_direction = bias if bias in {"bullish", "bearish"} and counts.get(bias, 0) >= minimum_support else vote_direction
    eligible = [item for item in ranked if item["direction"] == preferred_direction]
    if not eligible:
        eligible = ranked
    selected = max(eligible, key=lambda item: item["score_pct"])
    return selected["index"], ranked, counts, vote_direction, vote_pct


def ensemble_with_ict_ranking(
    paths: list[pd.DataFrame],
    *,
    base: float,
    timeframe: str,
    context: dict[str, Any],
    history: pd.DataFrame,
    future_timestamps: pd.Series,
    symbol: str,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    _, summary, gate = _BASE_ENSEMBLE(
        paths,
        base=base,
        timeframe=timeframe,
        context=context,
        history=history,
        future_timestamps=future_timestamps,
        symbol=symbol,
    )
    selected_index, ranked, counts, vote_direction, vote_pct = _rank_paths(
        paths,
        base=base,
        timeframe=timeframe,
        context=context,
    )
    projection = paths[selected_index].copy().reset_index(drop=True)
    ict = context.get("ict") or {}
    setup = ict.get("setup") or {}
    structure = ict.get("structure") or {}
    model = ict.get("context_model") or {}
    event = ict.get("event_risk") or {}
    selected = ranked[selected_index]
    selected_direction = selected["direction"]

    reasons: list[str] = []
    trade_allowed = bool(gate.get("trade_allowed", True))
    structure_bias = structure.get("bias")
    hierarchy_bias = ict.get("hierarchy_bias")
    if event.get("blocked"):
        trade_allowed = False
        reasons.append("High-impact economic event blackout.")
    if model.get("dominant") == "no_trade" and float(model.get("no_trade_probability_pct") or 0) >= 45:
        trade_allowed = False
        reasons.append("ICT context model favors no-trade.")
    if (
        structure_bias in {"bullish", "bearish"}
        and selected_direction in {"bullish", "bearish"}
        and selected_direction != structure_bias
        and float(structure.get("strength_pct") or 0) >= 55
    ):
        trade_allowed = False
        reasons.append("Selected Kronos path conflicts with strong market structure.")
    if (
        hierarchy_bias in {"bullish", "bearish"}
        and selected_direction in {"bullish", "bearish"}
        and selected_direction != hierarchy_bias
    ):
        trade_allowed = False
        reasons.append("Selected Kronos path conflicts with higher-timeframe structure.")
    if setup.get("state") in {"waiting", "blocked_event"}:
        trade_allowed = False
        reasons.append("ICT setup is not ready.")
    selected_support = counts.get(selected_direction, 0) / len(paths) * 100
    if selected_support < 30:
        trade_allowed = False
        reasons.append("Selected path lacks sufficient ensemble support.")

    gate = {
        **gate,
        "applied": bool(reasons) or bool(gate.get("applied")),
        "status": "ict_context_blocked" if reasons else "ict_context_passed",
        "reason": " ".join(reasons) if reasons else gate.get("reason"),
        "trade_allowed": trade_allowed,
        "projection_rewritten": False,
        "aggregation": RUNTIME_VERSION,
        "ict_version": ICT_VERSION,
        "selected_path_index": selected_index,
        "selected_path_score_pct": selected["score_pct"],
        "selected_path_direction": selected_direction,
        "selected_path_support_pct": round(selected_support, 1),
    }

    summary = {
        **summary,
        "aggregation": RUNTIME_VERSION,
        "projection_path_index": selected_index,
        "projection_is_real_sample": True,
        "ict_ranked": True,
        "ict_version": ICT_VERSION,
        "ranked_paths": sorted(ranked, key=lambda item: item["score_pct"], reverse=True),
        "selected_path": selected,
        "directional_vote": {
            **(summary.get("directional_vote") or {}),
            "direction": vote_direction,
            "agreement_pct": round(vote_pct, 1),
            "counts": counts,
        },
    }
    return projection, summary, gate


def matching_cache_with_ict(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    cached = _BASE_MATCHING_CACHE(*args, **kwargs)
    if not cached:
        return None
    revision = cached.get("revision") or {}
    ict = revision.get("ict_context") or ((revision.get("market_context") or {}).get("ict"))
    ensemble = revision.get("path_ensemble") or {}
    if not ict or ict.get("version") != ICT_VERSION:
        return None
    if ensemble.get("aggregation") != RUNTIME_VERSION:
        return None
    if not ensemble.get("projection_is_real_sample"):
        return None
    return cached


def _calibration_key(item: dict[str, Any]) -> tuple[str, str, str]:
    revision = item.get("revision") or {}
    market_context = revision.get("market_context") or {}
    ict = revision.get("ict_context") or market_context.get("ict") or {}
    structure = ict.get("structure") or {}
    session = ict.get("session") or {}
    return (
        str(market_context.get("regime") or "unknown"),
        str(structure.get("bias") or "sideways"),
        str(session.get("name") or "unknown"),
    )


def _context_calibration(store: PlatformStore, item: dict[str, Any]) -> dict[str, Any]:
    revision = item.get("revision") or {}
    ensemble = revision.get("path_ensemble") or {}
    vote = ensemble.get("directional_vote") or {}
    timeframe = str(item.get("timeframe") or "5m")
    horizon = int(vote.get("horizon_candles") or ONE_HOUR_HORIZONS.get(timeframe, 1))
    regime, structure_bias, session_name = _calibration_key(item)
    ict = revision.get("ict_context") or ((revision.get("market_context") or {}).get("ict")) or {}
    signature = f"{regime}|{structure_bias}|{session_name}"

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT s.forecast_id,s.direction_correct,s.close_error_pct,s.range_hit,
                   f.projection_json,f.revision_json
            FROM forecast_scores s
            JOIN forecasts f ON f.id=s.forecast_id
            WHERE f.symbol=? AND f.timeframe=? AND s.horizon=?
            ORDER BY s.scored_at DESC
            LIMIT 5000
            """,
            (normalize_symbol(str(item.get("symbol"))), timeframe, horizon),
        ).fetchall()

    samples: dict[str, dict[str, Any]] = {}
    for row in rows:
        stored_revision = json.loads(row["revision_json"]) if row["revision_json"] else {}
        stored_key = "|".join(_calibration_key({"revision": stored_revision, "timeframe": timeframe}))
        if stored_key != signature:
            continue
        intrabar = stored_revision.get("intrabar") or {}
        projection = json.loads(row["projection_json"])
        if not projection:
            continue
        base = intrabar.get("close")
        if base is None:
            continue
        target = float(projection[min(horizon, len(projection)) - 1]["close"])
        predicted_move_pct = abs((target - float(base)) / max(abs(float(base)), 1e-12) * 100)
        atr_pct = float(((stored_revision.get("market_context") or {}).get("volatility") or {}).get("atr_pct") or 0)
        tolerance = max(predicted_move_pct * 0.50, atr_pct * 0.35, 0.01)
        samples[row["forecast_id"]] = {
            "direction_correct": int(row["direction_correct"]),
            "distance_hit": float(row["close_error_pct"]) <= tolerance,
            "range_hit": int(row["range_hit"]),
        }

    count = len(samples)
    payload: dict[str, Any] = {
        "available": count >= MIN_CONTEXT_CALIBRATION_FORECASTS,
        "calibrated": count >= MIN_CONTEXT_CALIBRATION_FORECASTS,
        "independent_forecasts": count,
        "required_forecasts": MIN_CONTEXT_CALIBRATION_FORECASTS,
        "symbol": normalize_symbol(str(item.get("symbol"))),
        "timeframe": timeframe,
        "horizon": horizon,
        "regime": regime,
        "structure_bias": structure_bias,
        "session": session_name,
        "calibration_scope": "symbol+timeframe+horizon+regime+structure+session",
        "context_signature": ict.get("context_signature"),
        "path_vote": vote,
        "context_model": ict.get("context_model"),
        "paths": int(ensemble.get("paths") or (item.get("parameters") or {}).get("sample_count") or 1),
        "sample_count": int((item.get("parameters") or {}).get("sample_count") or 1),
    }
    if count < MIN_CONTEXT_CALIBRATION_FORECASTS:
        return payload

    values = list(samples.values())
    direction_accuracy = statistics.fmean(value["direction_correct"] for value in values) * 100
    distance_accuracy = statistics.fmean(int(value["distance_hit"]) for value in values) * 100
    range_accuracy = statistics.fmean(value["range_hit"] for value in values) * 100
    score = direction_accuracy * 0.60 + distance_accuracy * 0.25 + range_accuracy * 0.15
    payload.update(
        {
            "score_pct": round(score, 1),
            "grade": "high" if score >= 65 else "medium" if score >= 55 else "low",
            "components": {
                "direction_accuracy_pct": round(direction_accuracy, 1),
                "distance_accuracy_pct": round(distance_accuracy, 1),
                "range_accuracy_pct": round(range_accuracy, 1),
            },
        }
    )
    return payload


def _attach_ict_metadata(store: PlatformStore, item: dict[str, Any]) -> dict[str, Any]:
    enhanced = dict(item)
    revision = dict(enhanced.get("revision") or {})
    market_context = dict(revision.get("market_context") or {})
    ict = revision.get("ict_context") or market_context.get("ict")
    if not ict:
        return enhanced

    revision["ict_context"] = ict
    revision["context_model"] = ict.get("context_model")
    confidence = _context_calibration(store, {**enhanced, "revision": revision})
    revision["model_confidence"] = confidence
    enhanced["revision"] = revision
    enhanced["confidence"] = confidence
    enhanced["ict_context"] = ict
    enhanced["context_model"] = ict.get("context_model")
    if enhanced.get("uncertainty"):
        uncertainty = dict(enhanced["uncertainty"])
        uncertainty["confidence"] = confidence
        uncertainty["ict_context"] = ict
        enhanced["uncertainty"] = uncertainty
    return enhanced


def forecasts_with_ict(self: PlatformStore, symbol: str, timeframe: str, limit: int = 25) -> list[dict[str, Any]]:
    return [_attach_ict_metadata(self, item) for item in _BASE_STORE_FORECASTS(self, symbol, timeframe, limit)]


def forecast_with_ict(self: PlatformStore, forecast_id: str) -> dict[str, Any] | None:
    item = _BASE_STORE_FORECAST(self, forecast_id)
    return _attach_ict_metadata(self, item) if item else None


def _persist_enrichment(platform: ForecastPlatform, result: dict[str, Any]) -> dict[str, Any]:
    revision = dict(result.get("revision") or {})
    market_context = dict(revision.get("market_context") or {})
    ict = revision.get("ict_context") or market_context.get("ict")
    if not ict:
        return result

    revision["ict_context"] = ict
    revision["context_model"] = ict.get("context_model")
    revision["ict_version"] = ICT_VERSION
    parameters = dict(result.get("parameters") or {})
    parameters.update(
        {
            "aggregation": RUNTIME_VERSION,
            "ict_context": True,
            "ict_version": ICT_VERSION,
            "path_ranking": True,
            "liquidity_target_scoring": True,
            "structure_gate": True,
        }
    )
    result["parameters"] = parameters
    result["revision"] = revision
    result["ict_context"] = ict
    result["context_model"] = ict.get("context_model")
    result["regime_gate"] = revision.get("regime_gate") or result.get("regime_gate")

    confidence = _context_calibration(platform.store, {**result, "revision": revision, "parameters": parameters})
    revision["model_confidence"] = confidence
    result["confidence"] = confidence

    uncertainty = result.get("uncertainty")
    if uncertainty:
        uncertainty = dict(uncertainty)
        uncertainty["confidence"] = confidence
        uncertainty["ict_context"] = ict
        result["uncertainty"] = uncertainty

    forecast_id = result.get("id")
    if forecast_id:
        with platform.store._lock, platform.store.connection() as connection:
            connection.execute(
                """
                UPDATE forecasts
                SET parameters_json=?,revision_json=?,uncertainty_json=?
                WHERE id=?
                """,
                (
                    json.dumps(parameters, separators=(",", ":"), default=str),
                    json.dumps(revision, separators=(",", ":"), default=str),
                    json.dumps(uncertainty, separators=(",", ":"), default=str) if uncertainty else None,
                    forecast_id,
                ),
            )
    return result


def generate_with_ict(
    self: ForecastPlatform,
    params: ForecastParameters,
    *,
    advanced: bool = False,
    paths: int | None = None,
) -> dict[str, Any]:
    symbol = normalize_symbol(params.symbol)
    token = _RUNTIME_CONTEXT.set(
        {
            "platform": self,
            "symbol": symbol,
            "timeframe": params.timeframe,
            "events": _runtime_events(self, symbol),
            "higher_timeframes": _higher_timeframe_contexts(self, symbol, params.timeframe),
        }
    )
    try:
        result = _BASE_GENERATE(self, params, advanced=advanced, paths=paths)
    finally:
        _RUNTIME_CONTEXT.reset(token)
    return _persist_enrichment(self, result)


def _latest_ict(
    platform: ForecastPlatform,
    symbol: str,
    timeframe: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    items = platform.store.forecasts(symbol, timeframe, 1)
    if not items:
        return None, {}
    item = items[0]
    revision = item.get("revision") or {}
    ict = revision.get("ict_context") or ((revision.get("market_context") or {}).get("ict")) or {}
    return item, ict


def consensus_with_ict_hierarchy(
    self: ForecastPlatform,
    symbol: str,
    selected_timeframe: str,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)

    for timeframe in ("1h", "15m", "5m"):
        try:
            self.generate(
                ForecastParameters(
                    symbol=canonical,
                    timeframe=timeframe,
                    lookback=self.engine.settings.default_lookback,
                    pred_len=max(self.engine.settings.default_pred_len, 12),
                    sample_count=v2.NORMAL_SAMPLE_COUNT,
                ),
                advanced=False,
            )
        except Exception:
            pass

    base = dict(_BASE_CONSENSUS(self, canonical, selected_timeframe))
    items: dict[str, dict[str, Any] | None] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for timeframe in ALIGNED_TIMEFRAMES:
        item, ict = _latest_ict(self, canonical, timeframe)
        items[timeframe] = item
        contexts[timeframe] = ict

    one_hour = contexts["1h"]
    fifteen = contexts["15m"]
    five = contexts["5m"]

    one_structure = one_hour.get("structure") or {}
    one_model = one_hour.get("context_model") or {}
    htf_bias = one_structure.get("bias")
    if htf_bias not in {"bullish", "bearish"}:
        htf_bias = one_model.get("dominant")
    if htf_bias not in {"bullish", "bearish"}:
        htf_bias = "sideways"

    setup = fifteen.get("setup") or {}
    setup_bias = setup.get("bias", "sideways")
    setup_quality = float(setup.get("quality_pct") or 0)
    setup_state = setup.get("state", "waiting")

    five_liquidity = five.get("liquidity") or {}
    five_sweep = five_liquidity.get("sweep") or {}
    five_displacement = five.get("displacement") or {}
    five_structure = five.get("structure") or {}
    trigger_bias = (
        five_sweep.get("direction")
        if five_sweep.get("direction") in {"bullish", "bearish"}
        else five_displacement.get("direction")
        if five_displacement.get("active") and five_displacement.get("direction") in {"bullish", "bearish"}
        else five_structure.get("choch")
        or five_structure.get("bos")
        or (five.get("setup") or {}).get("bias")
    )
    if trigger_bias not in {"bullish", "bearish"}:
        trigger_bias = "sideways"

    event_block = any(bool((context.get("event_risk") or {}).get("blocked")) for context in contexts.values())
    contradiction = (
        htf_bias in {"bullish", "bearish"}
        and (
            (setup_bias in {"bullish", "bearish"} and setup_bias != htf_bias)
            or (trigger_bias in {"bullish", "bearish"} and trigger_bias != htf_bias)
        )
    )
    role_directions = [htf_bias, setup_bias, trigger_bias]
    agreement_count = sum(direction == htf_bias for direction in role_directions if htf_bias in {"bullish", "bearish"})
    agreement_pct = agreement_count / len(ALIGNED_TIMEFRAMES) * 100

    gates_allow = all(
        bool(((item or {}).get("revision") or {}).get("regime_gate", {}).get("trade_allowed", True))
        for item in items.values()
        if item
    )
    trigger_present = bool(
        five_sweep
        or five_displacement.get("active")
        or five_structure.get("choch")
        or five_structure.get("bos")
    )
    trade_allowed = (
        htf_bias in {"bullish", "bearish"}
        and not contradiction
        and not event_block
        and setup_bias == htf_bias
        and setup_quality >= 50
        and setup_state in {"ready", "developing"}
        and trigger_bias == htf_bias
        and trigger_present
        and gates_allow
    )

    if event_block:
        status = "event_block"
    elif contradiction:
        status = "conflict"
    elif trade_allowed:
        status = "aligned"
    elif htf_bias == "sideways":
        status = "no_htf_bias"
    elif setup_bias != htf_bias or setup_quality < 50:
        status = "waiting_15m_setup"
    else:
        status = "waiting_5m_trigger"

    readings_by_timeframe = {row.get("timeframe"): dict(row) for row in base.get("readings") or []}
    readings: list[dict[str, Any]] = []
    roles = {
        "5m": ("trigger", trigger_bias),
        "15m": ("setup", setup_bias),
        "1h": ("bias", htf_bias),
    }
    for timeframe in ALIGNED_TIMEFRAMES:
        role, direction = roles[timeframe]
        reading = readings_by_timeframe.get(timeframe, {"timeframe": timeframe})
        reading.update(
            {
                "role": role,
                "ict_direction": direction,
                "direction": direction,
                "ict_setup_state": (contexts[timeframe].get("setup") or {}).get("state"),
            }
        )
        readings.append(reading)

    selected_item = items.get(selected_timeframe) or items.get("15m")
    selected_revision = (selected_item or {}).get("revision") or {}
    selected_context = selected_revision.get("market_context") or {}

    hierarchy = {
        "1h": {
            "role": "directional_bias",
            "bias": htf_bias,
            "structure": one_structure.get("state"),
            "strength_pct": one_structure.get("strength_pct"),
        },
        "15m": {
            "role": "setup_location",
            "bias": setup_bias,
            "state": setup_state,
            "quality_pct": round(setup_quality, 1),
            "dealing_zone": (fifteen.get("dealing_range") or {}).get("zone"),
            "fvg": bool((fifteen.get("fair_value_gaps") or {}).get("nearest_bullish" if htf_bias == "bullish" else "nearest_bearish")),
        },
        "5m": {
            "role": "entry_trigger",
            "bias": trigger_bias,
            "trigger": (five.get("setup") or {}).get("trigger") or five_structure.get("choch") or five_structure.get("bos"),
            "displacement_pct": five_displacement.get("score_pct"),
        },
        "trade_allowed": trade_allowed,
        "status": status,
        "event_block": event_block,
    }

    return {
        **base,
        "selected": selected_timeframe,
        "target_window": "hierarchical",
        "readings": readings,
        "agreement_pct": round(agreement_pct, 1),
        "consensus": htf_bias if not contradiction else "conflict",
        "aligned": trade_allowed,
        "contradiction": contradiction,
        "complete": all(bool(context) for context in contexts.values()),
        "trade_bias": htf_bias if trade_allowed else "no_trade",
        "trade_allowed": trade_allowed,
        "alignment_status": status,
        "market_context": selected_context,
        "ict_context": contexts.get(selected_timeframe) or fifteen,
        "hierarchy": hierarchy,
    }


v2._market_context = market_context_with_ict
v2._ensemble = ensemble_with_ict_ranking
v2._matching_cache = matching_cache_with_ict
PlatformStore.forecasts = forecasts_with_ict  # type: ignore[method-assign]
PlatformStore.forecast = forecast_with_ict  # type: ignore[method-assign]
ForecastPlatform.generate = generate_with_ict  # type: ignore[method-assign]
ForecastPlatform.consensus = consensus_with_ict_hierarchy  # type: ignore[method-assign]
