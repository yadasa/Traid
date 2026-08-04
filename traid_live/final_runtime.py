from __future__ import annotations

# Import the alignment runtime first so the shared FastAPI application is built.
from .service_runtime import app

# Install MT5 serialization, forecast deduplication, and one shared WebSocket
# publisher per market/timeframe instead of one poller per browser tab.
from . import multitab_runtime as _multitab_runtime  # noqa: F401,E402

# Recover a shared publisher after a temporary MT5/provider failure without
# requiring every open browser tab to be refreshed.
from . import multitab_resilience as _multitab_resilience  # noqa: F401,E402

# Preserve Kronos paths, include intrabar input, use price-only index features,
# gate countertrend trades, align 5m/15m/1h, and calibrate confidence.
from . import intelligence_v2 as _intelligence_v2  # noqa: F401,E402

# Never replace blocked Kronos output with a hand-authored smooth continuation
# curve. Display one real sampled medoid trajectory and keep the regime gate as a
# trade decision only.
from . import trajectory_integrity as _trajectory_integrity  # noqa: F401,E402

# Add deterministic ICT/SMC structure, liquidity, FVG, order-block, session and
# event context. Rank genuine Kronos paths with that context, enforce the
# 1h/15m/5m hierarchy, and calibrate confidence by market context.
from . import ict_runtime as _ict_runtime  # noqa: F401,E402

# Poll and publish the latest MT5 quote independently from model inference,
# scoring, candle-boundary checks, and multi-timeframe analysis. The live candle
# close is forced to the exact quote price in the same WebSocket payload.
from . import live_priority_runtime as _live_priority_runtime  # noqa: F401,E402

__all__ = ["app"]
