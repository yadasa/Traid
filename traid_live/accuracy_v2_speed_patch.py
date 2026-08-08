from __future__ import annotations

import threading
import time
from typing import Any

import pandas as pd

from . import accuracy_v2_runtime as runtime

_ORIGINAL_MACRO_FRAME = runtime._macro_frame
_PENDING_LOCK = threading.RLock()
_PENDING: set[tuple[str, str]] = set()


def _background_refresh(name: str, timeframe: str, cutoff: pd.Timestamp) -> None:
    key = (name, timeframe)
    try:
        _ORIGINAL_MACRO_FRAME(name, timeframe, cutoff)
    except Exception:
        pass
    finally:
        with _PENDING_LOCK:
            _PENDING.discard(key)


def _schedule_refresh(name: str, timeframe: str, cutoff: pd.Timestamp) -> None:
    key = (name, timeframe)
    with _PENDING_LOCK:
        if key in _PENDING:
            return
        _PENDING.add(key)
    runtime._EXECUTOR.submit(_background_refresh, name, timeframe, cutoff)


def cached_macro_frame(name: str, timeframe: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Keep external HTTP off the live inference critical path.

    Historical Replay still requests the exact historical interval so no future
    data can leak into a simulated cutoff. Live predictions reuse the freshest
    pre-warmed macro frame; if a feed is temporarily missing, refresh it in the
    background and continue the forecast without waiting on network I/O.
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
            _schedule_refresh(name, timeframe, cutoff)
            return freshest[1].copy()

    _schedule_refresh(name, timeframe, cutoff)
    return pd.DataFrame()


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
# trader is waiting on Kronos. Missing feeds never delay a live forecast later;
# they refresh asynchronously and join the next prediction when available.
prewarm_live_references()
