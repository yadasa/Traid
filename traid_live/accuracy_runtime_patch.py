from __future__ import annotations

from typing import Any

from . import intelligence_v2 as v2
from .accuracy_runtime import RUNTIME_VERSION


_BASE_MATCHING_CACHE = v2._matching_cache


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


v2._matching_cache = matching_cache_with_accuracy_version
