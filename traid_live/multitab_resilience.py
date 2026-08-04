from __future__ import annotations

import asyncio

from . import multitab_runtime as runtime


_base_run_channel = runtime._run_channel


async def resilient_run_channel(channel: runtime.SharedChannel) -> None:
    """Restart a shared channel after a recoverable MT5/provider failure."""

    while channel.subscribers:
        try:
            await _base_run_channel(channel)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await runtime._broadcast(
                channel,
                {"type": "error", "detail": f"Market stream recovering: {exc}"},
            )
            await asyncio.sleep(1.0)


runtime._run_channel = resilient_run_channel
