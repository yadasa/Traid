from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from . import intelligence_v2 as v2
from . import ict_context as context_engine
from . import ict_runtime as runtime
from . import trajectory_integrity as trajectory
from .forecast import ForecastParameters
from .platform import ForecastPlatform, PlatformStore
from .ict_context import ICT_VERSION
from .ict_learning import adaptive_context_model
from .ict_sessions import session_levels, session_name


_ORIGINAL_MARKET_CONTEXT = runtime.market_context_with_ict
_RAW_MATCHING_CACHE = trajectory._ORIGINAL_MATCHING_CACHE
_ICT_GENERATE = ForecastPlatform.generate
_SCORE_REALIZED = PlatformStore.score_realized
_CALIBRATION_LOCK = threading.RLock()
_CALIBRATION_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


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


def generate_with_fresh_hierarchy(
    self: ForecastPlatform,
    params: ForecastParameters,
    *,
    advanced: bool = False,
    paths: int | None = None,
) -> dict[str, Any]:
    canonical = str(params.symbol).upper()
    auxiliary: tuple[str, ...]
    if params.timeframe == "5m":
        auxiliary = ("1h", "15m")
    elif params.timeframe == "15m":
        auxiliary = ("1h",)
    else:
        auxiliary = ()

    for timeframe in auxiliary:
        helper = replace(
            params,
            symbol=canonical,
            timeframe=timeframe,
            pred_len=max(int(params.pred_len), 12),
            sample_count=v2.NORMAL_SAMPLE_COUNT,
        )
        # Call the ICT generator directly. 1h is built first; the subsequent 15m
        # context therefore sees it, and the selected 5m context sees both.
        _ICT_GENERATE(self, helper, advanced=False, paths=None)

    return _ICT_GENERATE(
        self,
        replace(params, symbol=canonical),
        advanced=advanced,
        paths=paths,
    )


def raw_valid_forecasts(
    store: PlatformStore,
    symbol: str,
    timeframe: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    candidates = v2._raw_forecasts(store, symbol, timeframe, min(500, max(limit * 4, limit)))
    valid: list[dict[str, Any]] = []
    for item in candidates:
        gate = (item.get("revision") or {}).get("regime_gate") or {}
        if gate.get("fallback") == trajectory._SYNTHETIC_FALLBACK:
            continue
        valid.append(item)
        if len(valid) >= limit:
            break
    return valid


def raw_valid_forecast(store: PlatformStore, forecast_id: str) -> dict[str, Any] | None:
    item = v2._raw_forecast(store, forecast_id)
    if not item:
        return None
    gate = (item.get("revision") or {}).get("regime_gate") or {}
    return None if gate.get("fallback") == trajectory._SYNTHETIC_FALLBACK else item


def cached_context_calibration(
    store: PlatformStore,
    item: dict[str, Any],
) -> dict[str, Any]:
    revision = item.get("revision") or {}
    market_context = revision.get("market_context") or {}
    context = revision.get("ict_context") or market_context.get("ict") or {}
    ensemble = revision.get("path_ensemble") or {}
    vote = ensemble.get("directional_vote") or {}
    timeframe = str(item.get("timeframe") or "5m")
    horizon = int(vote.get("horizon_candles") or runtime.ONE_HOUR_HORIZONS.get(timeframe, 1))
    regime, structure_bias, session_name_value = runtime._calibration_key(item)
    key = (
        str(item.get("symbol")),
        timeframe,
        horizon,
        regime,
        structure_bias,
        session_name_value,
        context.get("version"),
    )
    with _CALIBRATION_LOCK:
        cached = _CALIBRATION_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    calculated = runtime._ORIGINAL_CONTEXT_CALIBRATION(store, item)
    with _CALIBRATION_LOCK:
        _CALIBRATION_CACHE[key] = dict(calculated)
    return calculated


def score_realized_and_invalidate(
    self: PlatformStore,
    symbol: str,
    timeframe: str,
    actual: Any,
) -> int:
    inserted = _SCORE_REALIZED(self, symbol, timeframe, actual)
    if inserted:
        with _CALIBRATION_LOCK:
            _CALIBRATION_CACHE.clear()
    return inserted


# Preserve the original implementation before replacing its global lookup with a
# cached wrapper. New realized scores clear the cache immediately.
runtime._ORIGINAL_CONTEXT_CALIBRATION = runtime._context_calibration
runtime._context_calibration = cached_context_calibration
runtime._BASE_STORE_FORECASTS = raw_valid_forecasts
runtime._BASE_STORE_FORECAST = raw_valid_forecast

# The analysis function resolves these module globals dynamically. Replacing them
# upgrades both session labels and session liquidity highs/lows without modifying
# the model's OHLC input contract.
context_engine._session_name = session_name
context_engine._session_levels = session_levels
runtime._BASE_MATCHING_CACHE = _RAW_MATCHING_CACHE
v2._market_context = market_context_with_adaptive_classifier
v2._matching_cache = matching_cache_with_context_identity
PlatformStore.score_realized = score_realized_and_invalidate  # type: ignore[method-assign]
ForecastPlatform.generate = generate_with_fresh_hierarchy  # type: ignore[method-assign]
