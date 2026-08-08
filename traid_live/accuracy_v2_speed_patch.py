from __future__ import annotations

import time
from typing import Any

import pandas as pd

from . import accuracy_v2_runtime as runtime

_ORIGINAL_MACRO_FRAME = runtime._macro_frame


def cached_macro_frame(name: str, timeframe: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Avoid live HTTP waits by reusing the freshest pre-warmed macro frame.

    Historical Replay still requests the exact historical interval so no future
    data can leak into a simulated cutoff. Live 15m/30m/4h contexts may safely
    reuse a fresh 5m/60m reference series because correlation alignment happens
    by timestamp rather than by positional candle index.
    """

    ticker = runtime.MACRO_TICKERS[name][0]
    live = abs((pd.Timestamp.now(tz="UTC") - cutoff).total_seconds()) < 900
    if not live:
        return _ORIGINAL_MACRO_FRAME(name, timeframe, cutoff)

    with runtime._MACRO_LOCK:
        exact = runtime._MACRO_CACHE.get((ticker, timeframe))
        if exact and time.monotonic() - exact[0] <= runtime.LIVE_MACRO_TTL_SECONDS:
            return exact[1].copy()

        candidates: list[tuple[float, pd.DataFrame]] = []
        for (cached_ticker, _cached_timeframe), cached in runtime._MACRO_CACHE.items():
            if cached_ticker != ticker:
                continue
            if time.monotonic() - cached[0] <= runtime.LIVE_MACRO_TTL_SECONDS:
                candidates.append(cached)
        if candidates:
            freshest = max(candidates, key=lambda item: item[0])
            return freshest[1].copy()

    # Startup prewarming normally makes this path unnecessary, but preserve a
    # correct blocking fallback rather than silently dropping a reference.
    return _ORIGINAL_MACRO_FRAME(name, timeframe, cutoff)


def prewarm_live_references() -> None:
    cutoff = pd.Timestamp.now(tz="UTC")
    futures: list[Any] = []
    for timeframe in ("1m", "5m", "1h"):
        for name in runtime.MACRO_TICKERS:
            futures.append(runtime._EXECUTOR.submit(_ORIGINAL_MACRO_FRAME, name, timeframe, cutoff))
    for future in futures:
        try:
            future.result()
        except Exception:
            pass


runtime._macro_frame = cached_macro_frame
runtime._prewarm_macro_cache = prewarm_live_references

# Pay the external-feed setup cost once during backend startup, not while the
# trader is waiting on Kronos. Failures are best-effort and never block startup
# permanently because every Yahoo request has a short timeout.
prewarm_live_references()
