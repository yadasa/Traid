from __future__ import annotations

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

_ORIGINAL_SEND_JSON = WebSocket.send_json


async def _safe_send_json(self: WebSocket, data, mode: str = "text") -> None:
    """Turn sends-after-disconnect into WebSocketDisconnect instead of RuntimeError.

    The service stream intentionally catches WebSocketDisconnect at its outer
    boundary. Starlette can otherwise raise RuntimeError on the second send made
    by a nested error handler after the browser has already gone away.
    """

    if self.application_state == WebSocketState.DISCONNECTED or self.client_state == WebSocketState.DISCONNECTED:
        raise WebSocketDisconnect(code=1006)
    try:
        await _ORIGINAL_SEND_JSON(self, data, mode=mode)
    except RuntimeError as exc:
        message = str(exc)
        if "Cannot call \"send\" once a close message has been sent" in message or "close message has been sent" in message:
            raise WebSocketDisconnect(code=1006) from None
        raise


WebSocket.send_json = _safe_send_json
