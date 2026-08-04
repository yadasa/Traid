from __future__ import annotations

# Import the alignment runtime first so its 5m/15m/1h consensus patch is active.
from .service_runtime import app

# Importing this module installs MT5 serialization, forecast deduplication, and
# replaces the per-tab WebSocket route on the same FastAPI application object.
from . import multitab_runtime as _multitab_runtime  # noqa: F401,E402

__all__ = ["app"]
