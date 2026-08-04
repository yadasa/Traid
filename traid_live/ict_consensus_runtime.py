from __future__ import annotations

from typing import Any

import pandas as pd

from . import intelligence_v2 as v2
from . import ict_runtime as runtime
from .forecast import ForecastParameters
from .market import normalize_symbol
from .platform import ForecastPlatform
from .ict_context import ICT_VERSION


ALIGNED_TIMEFRAMES = ("1h", "15m", "5m")
ONE_HOUR_HORIZONS = {"1h": 1, "15m": 4, "5m": 12}


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _context(item: dict[str, Any] | None) -> dict[str, Any]:
    revision = (item or {}).get("revision") or {}
    return revision.get("ict_context") or ((revision.get("market_context") or {}).get("ict")) or {}


def _signature(context: dict[str, Any] | None) -> str | None:
    return (context or {}).get("context_signature")


def _is_fresh(
    item: dict[str, Any] | None,
    *,
    completed_timestamp: str,
    current_timestamp: str,
    expected_higher: dict[str, dict[str, Any]],
) -> bool:
    if not item:
        return False
    revision = item.get("revision") or {}
    context = _context(item)
    ensemble = revision.get("path_ensemble") or {}
    try:
        same_completed = _timestamp(item.get("input_last_timestamp")) == completed_timestamp
    except Exception:
        same_completed = False
    same_current = (revision.get("intrabar") or {}).get("signature") == current_timestamp
    if not same_completed or not same_current:
        return False
    if context.get("version") != ICT_VERSION:
        return False
    if ensemble.get("aggregation") != runtime.RUNTIME_VERSION:
        return False
    if not ensemble.get("projection_is_real_sample"):
        return False

    cached_higher = context.get("higher_timeframes") or {}
    for timeframe, expected in expected_higher.items():
        if _signature(cached_higher.get(timeframe)) != _signature(expected):
            return False
    return not expected_higher or bool(cached_higher)


def _ensure_hierarchy_forecasts(
    platform: ForecastPlatform,
    symbol: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    items: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}

    for timeframe in ALIGNED_TIMEFRAMES:
        completed = platform.engine.candles(symbol, timeframe, 2)
        current = platform.engine.provider.get_current_candle(symbol, timeframe)
        completed_timestamp = _timestamp(completed["timestamp"].iloc[-1])
        current_timestamp = (
            _timestamp(current["timestamp"].iloc[-1])
            if current is not None and not current.empty
            else completed_timestamp
        )
        latest = platform.store.forecasts(symbol, timeframe, 1)
        item = latest[0] if latest else None
        expected_higher = {
            higher: contexts[higher]
            for higher in ("1h", "15m")
            if higher in contexts and (
                timeframe == "5m" or (timeframe == "15m" and higher == "1h")
            )
        }

        if not _is_fresh(
            item,
            completed_timestamp=completed_timestamp,
            current_timestamp=current_timestamp,
            expected_higher=expected_higher,
        ):
            result = platform.generate(
                ForecastParameters(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback=platform.engine.settings.default_lookback,
                    pred_len=max(platform.engine.settings.default_pred_len, 12),
                    sample_count=v2.NORMAL_SAMPLE_COUNT,
                ),
                advanced=False,
            )
            item = platform.store.forecast(result["id"]) or result

        if item:
            items[timeframe] = item
            contexts[timeframe] = _context(item)

    return items, contexts


def _forecast_reading(item: dict[str, Any] | None, timeframe: str) -> dict[str, Any]:
    if not item:
        return {
            "timeframe": timeframe,
            "direction": "unknown",
            "forecast_direction": "unknown",
            "move_pct": None,
            "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
        }
    history = item.get("history") or []
    projection = item.get("projection") or []
    if not history or not projection:
        return {
            "timeframe": timeframe,
            "direction": "unknown",
            "forecast_direction": "unknown",
            "move_pct": None,
            "horizon_candles": ONE_HOUR_HORIZONS[timeframe],
            "forecast_id": item.get("id"),
        }
    revision = item.get("revision") or {}
    intrabar = revision.get("intrabar") or {}
    market_context = revision.get("market_context") or {}
    base = float(intrabar.get("close", history[-1]["close"]))
    horizon = min(ONE_HOUR_HORIZONS[timeframe], len(projection))
    target = float(projection[horizon - 1]["close"])
    move_pct = (target - base) / max(abs(base), 1e-12) * 100
    threshold = max(
        float((market_context.get("volatility") or {}).get("atr_pct") or 0.0) * 0.10,
        0.002,
    )
    forecast_direction = (
        "bullish"
        if move_pct > threshold
        else "bearish"
        if move_pct < -threshold
        else "sideways"
    )
    return {
        "timeframe": timeframe,
        "forecast_direction": forecast_direction,
        "direction": forecast_direction,
        "move_pct": round(move_pct, 4),
        "horizon_candles": horizon,
        "target_window": "1h",
        "forecast_id": item.get("id"),
        "confidence": item.get("confidence"),
        "gate": revision.get("regime_gate"),
        "advanced": bool(item.get("uncertainty")),
    }


def consensus_without_duplicate_inference(
    self: ForecastPlatform,
    symbol: str,
    selected_timeframe: str,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    try:
        items, contexts = _ensure_hierarchy_forecasts(self, canonical)
    except Exception:
        # Return partial context instead of breaking the selected chart when an
        # auxiliary timeframe is temporarily unavailable.
        items, contexts = {}, {}
        for timeframe in ALIGNED_TIMEFRAMES:
            latest = self.store.forecasts(canonical, timeframe, 1)
            if latest:
                items[timeframe] = latest[0]
                contexts[timeframe] = _context(latest[0])

    one_hour = contexts.get("1h") or {}
    fifteen = contexts.get("15m") or {}
    five = contexts.get("5m") or {}

    one_structure = one_hour.get("structure") or {}
    one_model = one_hour.get("context_model") or {}
    htf_bias = one_structure.get("bias")
    if htf_bias not in {"bullish", "bearish"}:
        htf_bias = one_model.get("dominant")
    if htf_bias not in {"bullish", "bearish"}:
        htf_bias = "sideways"

    setup = fifteen.get("setup") or {}
    setup_bias = setup.get("bias", "sideways")
    setup_quality = float(setup.get("quality_pct") or 0.0)
    setup_state = setup.get("state", "waiting")

    five_liquidity = five.get("liquidity") or {}
    sweep = five_liquidity.get("sweep") or {}
    displacement = five.get("displacement") or {}
    five_structure = five.get("structure") or {}
    trigger_bias = (
        sweep.get("direction")
        if sweep.get("direction") in {"bullish", "bearish"}
        else displacement.get("direction")
        if displacement.get("active") and displacement.get("direction") in {"bullish", "bearish"}
        else five_structure.get("choch")
        or five_structure.get("bos")
        or (five.get("setup") or {}).get("bias")
    )
    if trigger_bias not in {"bullish", "bearish"}:
        trigger_bias = "sideways"

    roles = {
        "1h": ("bias", htf_bias),
        "15m": ("setup", setup_bias),
        "5m": ("trigger", trigger_bias),
    }
    readings: list[dict[str, Any]] = []
    for timeframe in ("5m", "15m", "1h"):
        reading = _forecast_reading(items.get(timeframe), timeframe)
        role, ict_direction = roles[timeframe]
        reading.update(
            {
                "role": role,
                "ict_direction": ict_direction,
                "direction": ict_direction,
                "ict_setup_state": (contexts.get(timeframe, {}).get("setup") or {}).get("state"),
            }
        )
        readings.append(reading)

    role_directions = [htf_bias, setup_bias, trigger_bias]
    agreement_count = sum(direction == htf_bias for direction in role_directions)
    agreement_pct = agreement_count / 3 * 100
    contradiction = htf_bias in {"bullish", "bearish"} and any(
        direction in {"bullish", "bearish"} and direction != htf_bias
        for direction in (setup_bias, trigger_bias)
    )
    event_block = any(
        bool((context.get("event_risk") or {}).get("blocked"))
        for context in contexts.values()
    )
    gates_allow = all(
        bool(((item.get("revision") or {}).get("regime_gate") or {}).get("trade_allowed", True))
        for item in items.values()
    )
    trigger_present = bool(
        sweep
        or displacement.get("active")
        or five_structure.get("choch")
        or five_structure.get("bos")
    )
    trade_allowed = (
        len(contexts) == 3
        and htf_bias in {"bullish", "bearish"}
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

    selected_item = items.get(selected_timeframe) or items.get("15m") or next(iter(items.values()), None)
    selected_revision = (selected_item or {}).get("revision") or {}
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
        },
        "5m": {
            "role": "entry_trigger",
            "bias": trigger_bias,
            "trigger": (five.get("setup") or {}).get("trigger") or five_structure.get("choch") or five_structure.get("bos"),
            "displacement_pct": displacement.get("score_pct"),
        },
        "trade_allowed": trade_allowed,
        "status": status,
        "event_block": event_block,
    }
    return {
        "selected": selected_timeframe,
        "target_window": "hierarchical",
        "readings": readings,
        "agreement_pct": round(agreement_pct, 1),
        "consensus": htf_bias if not contradiction else "conflict",
        "aligned": trade_allowed,
        "contradiction": contradiction,
        "complete": len(contexts) == 3,
        "trade_bias": htf_bias if trade_allowed else "no_trade",
        "trade_allowed": trade_allowed,
        "alignment_status": status,
        "market_context": selected_revision.get("market_context") or {},
        "ict_context": contexts.get(selected_timeframe) or fifteen,
        "hierarchy": hierarchy,
    }


ForecastPlatform.consensus = consensus_without_duplicate_inference  # type: ignore[method-assign]
