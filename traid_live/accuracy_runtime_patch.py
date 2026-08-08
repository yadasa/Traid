from __future__ import annotations

from typing import Any

from . import accuracy_runtime as accuracy
from . import intelligence_v2 as v2
from .accuracy_runtime import RUNTIME_VERSION


_BASE_MATCHING_CACHE = v2._matching_cache
_BASE_RECORD_REPLAY_OUTCOME = accuracy.record_replay_outcome


def matching_cache_with_accuracy_version(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Do not reuse forecasts created before cross-market/learned path ranking."""

    cached = _BASE_MATCHING_CACHE(*args, **kwargs)
    if not cached:
        return None
    revision = cached.get("revision") or {}
    market_context = revision.get("market_context") or {}
    ict_context = revision.get("ict_context") or market_context.get("ict") or {}
    cross_market = market_context.get("cross_market") or ict_context.get("cross_market") or {}
    ensemble = revision.get("path_ensemble") or {}
    selected = ensemble.get("selected_path") or {}

    if cross_market.get("version") != RUNTIME_VERSION:
        return None
    if "cross_market_alignment_pct" not in selected:
        return None
    if "learning_samples" not in selected:
        return None
    return cached


def record_complete_replay_outcome(
    target: Any,
    capture: dict[str, Any] | None,
    actual: Any,
    *,
    cutoff_timestamp: str,
    atr: float | None,
) -> dict[str, Any]:
    """Never train the selector on a forecast whose full future is not realized."""

    entries = (capture or {}).get("entries") or []
    expected = min(
        (len(entry.get("path") or []) for entry in entries if entry.get("path")),
        default=0,
    )
    actual_count = len(actual) if actual is not None else 0
    if expected and actual_count < expected:
        return {
            "recorded": 0,
            "learned": False,
            "partial_outcome_skipped": True,
            "realized_candles": actual_count,
            "required_candles": expected,
            "detail": "Path learning waits until the complete replay forecast horizon is realized.",
            "runtime_version": RUNTIME_VERSION,
        }
    return _BASE_RECORD_REPLAY_OUTCOME(
        target,
        capture,
        actual,
        cutoff_timestamp=cutoff_timestamp,
        atr=atr,
    )


v2._matching_cache = matching_cache_with_accuracy_version
accuracy.record_replay_outcome = record_complete_replay_outcome
