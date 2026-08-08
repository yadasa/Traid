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

# Reuse ICT forecasts safely within the same candle while invalidating them when
# higher-timeframe context changes. Blend the deterministic context model with a
# compact classifier learned from Traid's own realized forecast outcomes.
from . import ict_runtime_patch as _ict_runtime_patch  # noqa: F401,E402

# Build hierarchy context without generating a second normal forecast when a
# fresh Advanced forecast already exists for the same candle.
from . import ict_consensus_runtime as _ict_consensus_runtime  # noqa: F401,E402

# Add completed-candle cross-market context and an online path-quality model that
# learns which genuine Kronos candidates perform best from historical Replay
# outcomes. This patches the ICT ranking hook without replacing Kronos output.
from . import accuracy_runtime as _accuracy_runtime  # noqa: F401,E402

# Reject same-candle forecasts created before the accuracy runtime so deployment
# starts using cross-market/learned ranking immediately instead of after one bar.
from . import accuracy_runtime_patch as _accuracy_runtime_patch  # noqa: F401,E402

# Populate the existing economic-event store from the public Forex Factory weekly
# export, refresh it hourly, and map each event to affected Traid symbols.
from . import calendar_runtime as _calendar_runtime  # noqa: F401,E402

# Expose one-shot historical Kronos inference at a fixed completed-candle cutoff.
# The realized future is returned separately for cheap client-side replay and is
# never included in the model input. Replay outcomes also train the path selector.
from . import replay_runtime as _replay_runtime  # noqa: F401,E402

# Poll and publish the latest MT5 quote independently from model inference,
# scoring, candle-boundary checks, and multi-timeframe analysis. The live candle
# close is forced to the exact quote price in the same WebSocket payload.
from . import live_priority_runtime as _live_priority_runtime  # noqa: F401,E402

__all__ = ["app"]
