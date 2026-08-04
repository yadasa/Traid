from __future__ import annotations

# Import the alignment runtime first so its 5m/15m/1h consensus patch is active.
from .service_runtime import app

# Install MT5 serialization, forecast deduplication, and one shared WebSocket
# publisher per market/timeframe instead of one poller per browser tab.
from . import multitab_runtime as _multitab_runtime  # noqa: F401,E402

# Recover a shared publisher after a temporary MT5/provider failure without
# requiring every open browser tab to be refreshed.
from . import multitab_resilience as _multitab_resilience  # noqa: F401,E402

__all__ = ["app"]
