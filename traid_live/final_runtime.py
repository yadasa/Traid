from __future__ import annotations

# Import the alignment runtime first so the shared FastAPI application is built.
from .service_runtime import app

# Install MT5 serialization, forecast deduplication, and one shared WebSocket
# publisher per market/timeframe instead of one poller per browser tab.
from . import multitab_runtime as _multitab_runtime  # noqa: F401,E402

# Recover a shared publisher after a temporary MT5/provider failure without
# requiring every open browser tab to be refreshed.
from . import multitab_resilience as _multitab_resilience  # noqa: F401,E402

# Replace averaged Kronos output with preserved path voting, intrabar inputs,
# price-only index features, regime gating, strict 5m/15m/1h alignment, and
# regime-specific empirical confidence calibration.
from . import intelligence_v2 as _intelligence_v2  # noqa: F401,E402

__all__ = ["app"]
