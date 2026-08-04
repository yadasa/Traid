from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np
import pandas as pd

from .platform import PlatformStore
from . import intelligence_v2 as v2


AGGREGATION_VERSION = "sampled_medoid_v3"
_SYNTHETIC_FALLBACK = "momentum_continuation_proxy"
_ORIGINAL_MATCHING_CACHE = v2._matching_cache
_ORIGINAL_STORE_FORECASTS = PlatformStore.forecasts


def _representative_medoid(paths: list[pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    """Return one real sampled path closest to the pointwise median trajectory.

    A pointwise median can splice ten different paths together candle-by-candle and
    create an unnaturally smooth synthetic curve. The medoid keeps the median idea
    but selects the closest complete trajectory that Kronos actually sampled.
    """

    if not paths:
        raise ValueError("At least one sampled path is required.")
    length = min(len(path) for path in paths)
    close_matrix = np.asarray(
        [path.iloc[:length]["close"].astype(float).to_numpy() for path in paths],
        dtype=float,
    )
    pointwise_median = np.median(close_matrix, axis=0)
    scale = max(float(np.ptp(pointwise_median)), abs(float(pointwise_median[-1])) * 1e-6, 1e-9)
    distances = np.mean(np.abs(close_matrix - pointwise_median), axis=1) / scale
    index = int(np.argmin(distances))
    return paths[index].iloc[:length].copy().reset_index(drop=True), index


def ensemble_without_synthetic_curve(
    paths: list[pd.DataFrame],
    *,
    base: float,
    timeframe: str,
    context: dict[str, Any],
    history: pd.DataFrame,
    future_timestamps: pd.Series,
    symbol: str,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Vote over paths, display a real path, and gate trades without redrawing price.

    Regime analysis is allowed to block a trade signal. It is never allowed to
    replace Kronos output with a hand-authored continuation curve.
    """

    del history, future_timestamps, symbol  # Context only; never synthesize a path.
    if not paths:
        raise ValueError("At least one forecast path is required.")

    horizon = min(v2.ONE_HOUR_HORIZONS.get(timeframe, len(paths[0])), len(paths[0]))
    threshold_pct = max(
        float(context.get("volatility", {}).get("atr_pct") or 0.0) * 0.10,
        0.002,
    )
    directions = [
        v2._path_direction(path, base, horizon, threshold_pct)
        for path in paths
    ]
    counts = {
        name: directions.count(name)
        for name in ("bullish", "bearish", "sideways")
    }
    vote_direction = max(counts, key=counts.get)
    vote_pct = counts[vote_direction] / len(paths) * 100

    trend = context.get("trend") or {}
    breakout = context.get("breakout") or {}
    trend_direction = trend.get("direction", "unknown")
    trend_strength = float(trend.get("strength_pct") or 0.0)
    strong_regime = (
        trend_direction in {"bullish", "bearish"}
        and trend_strength >= 55
        and (
            bool(breakout.get("active"))
            or str(context.get("regime", "")).endswith("_trend")
        )
    )
    opposite = (
        vote_direction in {"bullish", "bearish"}
        and trend_direction in {"bullish", "bearish"}
        and vote_direction != trend_direction
    )

    gate: dict[str, Any] = {
        "applied": False,
        "status": "passed",
        "reason": None,
        "trend_direction": trend_direction,
        "trend_strength_pct": round(trend_strength, 1),
        "raw_vote_direction": vote_direction,
        "raw_vote_pct": round(vote_pct, 1),
        "selected_paths": len(paths),
        "total_paths": len(paths),
        "trade_allowed": True,
        "projection_rewritten": False,
        "aggregation": AGGREGATION_VERSION,
    }

    if strong_regime and opposite:
        gate.update(
            {
                "applied": True,
                "status": "countertrend_forecast_blocked",
                "reason": "Kronos path vote opposes the strong trend/breakout regime.",
                "trade_allowed": False,
            }
        )
    elif strong_regime and vote_direction == "sideways":
        gate.update(
            {
                "applied": True,
                "status": "inconclusive_in_strong_regime",
                "reason": "Kronos paths are indecisive during a strong directional regime.",
                "trade_allowed": False,
            }
        )
    elif vote_pct < 50:
        gate.update(
            {
                "applied": True,
                "status": "low_path_agreement",
                "reason": "No path direction has a majority.",
                "trade_allowed": False,
            }
        )

    projection, medoid_index = _representative_medoid(paths)
    p25 = v2._aggregate_paths(paths, 0.25)
    p75 = v2._aggregate_paths(paths, 0.75)
    pointwise_median = v2._aggregate_paths(paths, 0.50)
    minimum_length = min(len(path) for path in paths)

    summary = {
        "paths": len(paths),
        "selected_paths": len(paths),
        "aggregation": AGGREGATION_VERSION,
        "projection_path_index": medoid_index,
        "projection_is_real_sample": True,
        "median": v2._records(pointwise_median),
        "p25": v2._records(p25),
        "p75": v2._records(p75),
        "p10": v2._records(v2._aggregate_paths(paths, 0.10)),
        "p90": v2._records(v2._aggregate_paths(paths, 0.90)),
        "directional_vote": {
            "direction": vote_direction,
            "agreement_pct": round(vote_pct, 1),
            "counts": counts,
            "horizon_candles": horizon,
            "target_window": "1h",
        },
        "bullish_probability": [
            sum(float(path.iloc[index]["close"]) >= base for path in paths)
            / len(paths)
            * 100
            for index in range(minimum_length)
        ],
        "mean_iqr_width": statistics.fmean(
            max(
                0.0,
                float(p75.iloc[index]["close"])
                - float(p25.iloc[index]["close"]),
            )
            for index in range(minimum_length)
        ),
    }
    return projection, summary, gate


def cache_without_synthetic_fallback(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    cached = _ORIGINAL_MATCHING_CACHE(*args, **kwargs)
    if not cached:
        return None
    revision = cached.get("revision") or {}
    gate = revision.get("regime_gate") or {}
    ensemble = revision.get("path_ensemble") or {}
    if gate.get("fallback") == _SYNTHETIC_FALLBACK:
        return None
    if ensemble.get("aggregation") != AGGREGATION_VERSION:
        return None
    if not ensemble.get("projection_is_real_sample"):
        return None
    return cached


def forecasts_without_synthetic_fallback(
    self: PlatformStore,
    symbol: str,
    timeframe: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    # Ask for extra rows so removing invalid synthetic projections does not leave
    # the dashboard with an unnecessarily short history.
    candidates = _ORIGINAL_STORE_FORECASTS(self, symbol, timeframe, min(500, max(limit * 4, limit)))
    valid: list[dict[str, Any]] = []
    for item in candidates:
        revision = item.get("revision") or {}
        gate = revision.get("regime_gate") or {}
        if gate.get("fallback") == _SYNTHETIC_FALLBACK:
            continue
        valid.append(item)
        if len(valid) >= limit:
            break
    return valid


# generate_v2 resolves these names dynamically, so replacing the module globals
# corrects both HTTP forecasts and shared-WebSocket refreshes immediately.
v2._ensemble = ensemble_without_synthetic_curve
v2._matching_cache = cache_without_synthetic_fallback
PlatformStore.forecasts = forecasts_without_synthetic_fallback  # type: ignore[method-assign]
