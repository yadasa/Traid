from __future__ import annotations

from typing import Any

from . import intelligence_v2 as v2
from . import ict_runtime as runtime
from . import trajectory_integrity as trajectory
from .ict_context import ICT_VERSION
from .ict_learning import adaptive_context_model


_ORIGINAL_MARKET_CONTEXT = runtime.market_context_with_ict
_RAW_MATCHING_CACHE = trajectory._ORIGINAL_MATCHING_CACHE


def _signature(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    return context.get("context_signature")


def market_context_with_adaptive_classifier(frame: Any) -> dict[str, Any]:
    market_context = _ORIGINAL_MARKET_CONTEXT(frame)
    context = market_context.get("ict") or {}
    state = runtime._RUNTIME_CONTEXT.get() or {}
    platform = state.get("platform")
    if not context or platform is None:
        return market_context

    timeframe = str(state.get("timeframe") or "5m")
    horizon = int(runtime.ONE_HOUR_HORIZONS.get(timeframe, 1))
    heuristic = context.get("context_model") or {}
    try:
        context["context_model"] = adaptive_context_model(
            platform.store,
            symbol=str(state.get("symbol") or "UNKNOWN"),
            timeframe=timeframe,
            horizon=horizon,
            context=context,
            market_context=market_context,
            heuristic=heuristic,
        )
    except Exception as exc:
        # Forecast generation must remain available even if historical learning
        # data are malformed or the local database is temporarily locked.
        context["context_model"] = {
            **heuristic,
            "mode": "heuristic_recovery",
            "learned": False,
            "detail": str(exc),
        }
    market_context["ict"] = context
    return market_context


def matching_cache_with_context_identity(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    # Use intelligence_v2's raw identity matcher rather than trajectory_integrity's
    # previous aggregation-version guard. New ICT forecasts use their own genuine
    # sampled-path aggregation version and must still be reusable within a candle.
    cached = _RAW_MATCHING_CACHE(*args, **kwargs)
    if not cached:
        return None

    revision = cached.get("revision") or {}
    market_context = revision.get("market_context") or {}
    context = revision.get("ict_context") or market_context.get("ict") or {}
    ensemble = revision.get("path_ensemble") or {}
    if context.get("version") != ICT_VERSION:
        return None
    if ensemble.get("aggregation") != runtime.RUNTIME_VERSION:
        return None
    if not ensemble.get("projection_is_real_sample"):
        return None

    current = runtime._RUNTIME_CONTEXT.get() or {}
    expected_higher = current.get("higher_timeframes") or {}
    cached_higher = context.get("higher_timeframes") or {}
    for timeframe, expected_context in expected_higher.items():
        if _signature(cached_higher.get(timeframe)) != _signature(expected_context):
            return None

    # A forecast made before top-down context became available must not be reused
    # once a fresh higher-timeframe hierarchy exists.
    if expected_higher and not cached_higher:
        return None
    return cached


runtime._BASE_MATCHING_CACHE = _RAW_MATCHING_CACHE
v2._market_context = market_context_with_adaptive_classifier
v2._matching_cache = matching_cache_with_context_identity
