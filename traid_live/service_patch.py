from __future__ import annotations

from fastapi.routing import APIWebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .service import app, stream as original_stream


# Replace the original route with a wrapper that treats a browser-initiated
# symbol/timeframe switch as a normal disconnect instead of an ASGI error.
app.router.routes = [
    route
    for route in app.router.routes
    if not (
        isinstance(route, APIWebSocketRoute)
        and route.path == "/v1/stream/{symbol}"
    )
]


@app.websocket("/v1/stream/{symbol}")
async def safe_stream(
    websocket: WebSocket,
    symbol: str,
    timeframe: str = "5m",
    with_forecast: bool = False,
    advanced: bool = False,
    pred_len: int = 24,
) -> None:
    try:
        await original_stream(
            websocket=websocket,
            symbol=symbol,
            timeframe=timeframe,
            with_forecast=with_forecast,
            advanced=advanced,
            pred_len=pred_len,
        )
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        message = str(exc).lower()
        if "close message has been sent" in message or "websocket" in message and "closed" in message:
            return
        raise
