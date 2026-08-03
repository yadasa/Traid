from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState


SendJson = Callable[[WebSocket, Any, str], Awaitable[None]]
_installed = False


def install_websocket_disconnect_guard() -> None:
    """Turn sends on an already-closed socket into a normal disconnect.

    The dashboard intentionally closes its previous market stream when the user
    changes symbol or timeframe. Depending on the Starlette/Uvicorn versions,
    a send racing that close can surface as RuntimeError instead of
    WebSocketDisconnect. Normalizing it prevents the stream endpoint from
    attempting to send an error over the same closed connection.
    """

    global _installed
    if _installed:
        return

    original_send_json = WebSocket.send_json

    async def guarded_send_json(
        self: WebSocket,
        data: Any,
        mode: str = "text",
    ) -> None:
        if self.application_state is WebSocketState.DISCONNECTED:
            raise WebSocketDisconnect(code=1000)

        try:
            await original_send_json(self, data, mode=mode)
        except RuntimeError as exc:
            closed = (
                self.application_state is WebSocketState.DISCONNECTED
                or "once a close message has been sent" in str(exc)
            )
            if closed:
                raise WebSocketDisconnect(code=1000) from exc
            raise

    WebSocket.send_json = guarded_send_json  # type: ignore[method-assign]
    _installed = True
