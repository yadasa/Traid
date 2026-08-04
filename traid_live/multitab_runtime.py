from __future__ import annotations

import asyncio
import functools
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

import pandas as pd
from fastapi.routing import APIWebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .forecast import ForecastParameters
from .market import normalize_symbol
from .platform import ForecastPlatform
from .service import (
    frame_records,
    get_engine,
    get_platform,
    settings,
    store,
    utc_now_iso,
)
from .service_patch import (
    ADVANCED_PATH_COUNT,
    NORMAL_SAMPLE_COUNT,
    app,
)


# MetaTrader5's Python bridge uses shared terminal state and is not reliable when
# several request threads call symbol_select/copy_rates/tick APIs simultaneously.
# Serialize those calls process-wide while still allowing model inference and the
# FastAPI event loop to run concurrently.
_MT5_IO_LOCK = threading.RLock()
_PROVIDER_METHODS = ("get_quote", "get_current_candle", "get_candles")


def _install_provider_serialization() -> None:
    provider = get_engine().provider
    if provider.name != "mt5" or getattr(provider, "_traid_multitab_serialized", False):
        return

    for method_name in _PROVIDER_METHODS:
        original = getattr(provider, method_name)

        @functools.wraps(original)
        def guarded(*args: Any, __original=original, **kwargs: Any):
            with _MT5_IO_LOCK:
                return __original(*args, **kwargs)

        setattr(provider, method_name, guarded)

    setattr(provider, "_traid_multitab_serialized", True)


_install_provider_serialization()


# The intelligence layer already enforces 10 normal samples, 14 advanced paths,
# confidence metadata, and market context. Wrap it with an identity-based lock so
# simultaneous tabs reuse the same completed-candle forecast instead of running
# duplicate GPU jobs.
_INTELLIGENCE_GENERATE = ForecastPlatform.generate
_GENERATION_LOCKS_GUARD = threading.Lock()
_GENERATION_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _matching_cached_forecast(
    platform: ForecastPlatform,
    *,
    symbol: str,
    timeframe: str,
    input_last_timestamp: str,
    params: ForecastParameters,
    advanced: bool,
    paths: int | None,
) -> dict[str, Any] | None:
    for item in platform.store.forecasts(symbol, timeframe, 30):
        parameters = item.get("parameters") or {}
        try:
            same_input = _timestamp(item.get("input_last_timestamp")) == input_last_timestamp
        except Exception:
            same_input = False
        same_parameters = (
            int(parameters.get("lookback", -1)) == int(params.lookback)
            and int(parameters.get("pred_len", -1)) == int(params.pred_len)
            and int(parameters.get("sample_count", -1)) == int(params.sample_count)
            and float(parameters.get("temperature", -1)) == float(params.temperature)
            and int(parameters.get("top_k", -1)) == int(params.top_k)
            and float(parameters.get("top_p", -1)) == float(params.top_p)
        )
        uncertainty = item.get("uncertainty")
        same_mode = (
            bool(uncertainty)
            and int((uncertainty or {}).get("paths", -1)) == int(paths or ADVANCED_PATH_COUNT)
            if advanced
            else not uncertainty
        )
        if (
            same_input
            and item.get("model_id") == platform.engine.settings.model_id
            and same_parameters
            and same_mode
        ):
            return item
    return None


def _generation_result_from_cache(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "symbol": item["symbol"],
        "timeframe": item["timeframe"],
        "generated_at": item["generated_at"],
        "input_last_timestamp": item["input_last_timestamp"],
        "history": item.get("history") or [],
        "projection": item.get("projection") or [],
        "uncertainty": item.get("uncertainty"),
        "revision": item.get("revision"),
        "parameters": item.get("parameters") or {},
        "confidence": item.get("confidence"),
        "advanced": bool(item.get("uncertainty")),
        "inference_ms": item.get("inference_ms", 0),
        "reused": True,
    }


def generate_once_per_candle(
    self: ForecastPlatform,
    params: ForecastParameters,
    *,
    advanced: bool = False,
    paths: int | None = None,
) -> dict[str, Any]:
    effective = replace(
        params,
        sample_count=max(NORMAL_SAMPLE_COUNT, int(params.sample_count)),
    )
    effective_paths = max(ADVANCED_PATH_COUNT, int(paths or 0)) if advanced else None
    canonical = normalize_symbol(effective.symbol)

    latest = self.engine.candles(canonical, effective.timeframe, 2)
    input_last_timestamp = _timestamp(latest["timestamp"].iloc[-1])
    identity = (
        canonical,
        effective.timeframe,
        input_last_timestamp,
        self.engine.settings.model_id,
        self.engine.settings.tokenizer_id,
        effective.lookback,
        effective.pred_len,
        effective.sample_count,
        effective.temperature,
        effective.top_k,
        effective.top_p,
        advanced,
        effective_paths,
    )

    with _GENERATION_LOCKS_GUARD:
        generation_lock = _GENERATION_LOCKS.setdefault(identity, threading.Lock())

    with generation_lock:
        cached = _matching_cached_forecast(
            self,
            symbol=canonical,
            timeframe=effective.timeframe,
            input_last_timestamp=input_last_timestamp,
            params=effective,
            advanced=advanced,
            paths=effective_paths,
        )
        if cached:
            return _generation_result_from_cache(cached)

        result = _INTELLIGENCE_GENERATE(
            self,
            effective,
            advanced=advanced,
            paths=effective_paths,
        )
        result["input_last_timestamp"] = input_last_timestamp
        return result


ForecastPlatform.generate = generate_once_per_candle  # type: ignore[method-assign]


@dataclass(frozen=True)
class StreamKey:
    symbol: str
    timeframe: str
    with_forecast: bool
    advanced: bool
    pred_len: int


@dataclass
class SharedChannel:
    key: StreamKey
    subscribers: set[asyncio.Queue[dict[str, Any]]]
    task: asyncio.Task[None] | None = None
    last_market: dict[str, Any] | None = None
    last_projection: dict[str, Any] | None = None


_CHANNELS: dict[StreamKey, SharedChannel] = {}
_CHANNELS_LOCK = asyncio.Lock()


def _queue_latest(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        pass


async def _broadcast(channel: SharedChannel, payload: dict[str, Any]) -> None:
    if payload.get("type") == "market_update":
        channel.last_market = payload
    elif payload.get("type") == "projection_update":
        channel.last_projection = payload
    for queue in tuple(channel.subscribers):
        _queue_latest(queue, payload)


async def _run_channel(channel: SharedChannel) -> None:
    key = channel.key
    engine = get_engine()
    forecast_task: asyncio.Task[dict[str, Any]] | None = None
    score_task: asyncio.Task[int] | None = None
    refresh_queued = False

    try:
        initial = await asyncio.to_thread(engine.candles, key.symbol, key.timeframe, 2)
        last_completed = _timestamp(initial["timestamp"].iloc[-1])
        next_bar_check = 0.0
        quote_poll = (
            max(settings.quote_poll_seconds, 2.0)
            if engine.provider.name == "massive"
            else settings.quote_poll_seconds
        )

        def start_forecast() -> asyncio.Task[dict[str, Any]]:
            params = ForecastParameters(
                symbol=key.symbol,
                timeframe=key.timeframe,
                lookback=settings.default_lookback,
                pred_len=key.pred_len,
                sample_count=NORMAL_SAMPLE_COUNT,
            )
            path_count = (
                store.get_setting("uncertainty_paths", ADVANCED_PATH_COUNT)
                if key.advanced
                else None
            )
            return asyncio.create_task(
                asyncio.to_thread(
                    get_platform().generate,
                    params,
                    advanced=key.advanced,
                    paths=path_count,
                )
            )

        while True:
            loop_started = time.monotonic()
            try:
                live_quote = await asyncio.to_thread(engine.provider.get_quote, key.symbol)
                current = await asyncio.to_thread(
                    engine.provider.get_current_candle,
                    key.symbol,
                    key.timeframe,
                )
                payload: dict[str, Any] = {
                    "type": "market_update",
                    "symbol": key.symbol,
                    "timeframe": key.timeframe,
                    "quote": live_quote.to_dict(),
                    "current_candle": (
                        frame_records(current)[0]
                        if current is not None and not current.empty
                        else None
                    ),
                    "server_timestamp": utc_now_iso(),
                }

                now = time.monotonic()
                if now >= next_bar_check:
                    latest_frame = await asyncio.to_thread(
                        engine.candles,
                        key.symbol,
                        key.timeframe,
                        2,
                    )
                    latest = latest_frame.tail(1)
                    latest_timestamp = _timestamp(latest["timestamp"].iloc[0])
                    if latest_timestamp != last_completed:
                        payload["completed_candle"] = frame_records(latest)[0]
                        last_completed = latest_timestamp

                        # Scoring is useful but must never pause the live price feed.
                        if score_task is None or score_task.done():
                            score_task = asyncio.create_task(
                                asyncio.to_thread(
                                    get_platform().score_market,
                                    key.symbol,
                                    key.timeframe,
                                )
                            )

                        if key.with_forecast:
                            if forecast_task is None:
                                forecast_task = start_forecast()
                                payload["forecast_status"] = "refreshing"
                            else:
                                refresh_queued = True
                                payload["forecast_status"] = "queued"
                    next_bar_check = now + settings.bar_poll_seconds

                await _broadcast(channel, payload)

                if forecast_task is not None and forecast_task.done():
                    try:
                        result = forecast_task.result()
                        result.update(
                            {
                                "type": "projection_update",
                                "accuracy": store.accuracy(key.symbol, key.timeframe),
                            }
                        )
                        await _broadcast(channel, result)
                    except Exception as exc:
                        await _broadcast(
                            channel,
                            {"type": "forecast_error", "detail": str(exc)},
                        )
                    finally:
                        forecast_task = None

                    if refresh_queued:
                        refresh_queued = False
                        forecast_task = start_forecast()
                        await _broadcast(
                            channel,
                            {"type": "forecast_status", "status": "refreshing_queued"},
                        )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _broadcast(channel, {"type": "error", "detail": str(exc)})

            await asyncio.sleep(
                max(0.05, quote_poll - (time.monotonic() - loop_started))
            )
    finally:
        if forecast_task is not None and not forecast_task.done():
            forecast_task.cancel()
        if score_task is not None and not score_task.done():
            score_task.cancel()


async def _subscribe(key: StreamKey) -> tuple[SharedChannel, asyncio.Queue[dict[str, Any]]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    async with _CHANNELS_LOCK:
        channel = _CHANNELS.get(key)
        if channel is None or channel.task is None or channel.task.done():
            channel = SharedChannel(key=key, subscribers=set())
            _CHANNELS[key] = channel
            channel.task = asyncio.create_task(_run_channel(channel))
        channel.subscribers.add(queue)
        if channel.last_market:
            _queue_latest(queue, channel.last_market)
        if channel.last_projection:
            _queue_latest(queue, channel.last_projection)
    return channel, queue


async def _unsubscribe(
    channel: SharedChannel,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    task_to_cancel: asyncio.Task[None] | None = None
    async with _CHANNELS_LOCK:
        channel.subscribers.discard(queue)
        if not channel.subscribers and _CHANNELS.get(channel.key) is channel:
            _CHANNELS.pop(channel.key, None)
            task_to_cancel = channel.task
    if task_to_cancel is not None and not task_to_cancel.done():
        task_to_cancel.cancel()
        try:
            await task_to_cancel
        except asyncio.CancelledError:
            pass


# Replace the per-browser stream route installed by service_patch with the shared
# fan-out stream above. Opening ten tabs now creates ten subscribers, not ten MT5
# pollers or ten Kronos jobs.
app.router.routes = [
    route
    for route in app.router.routes
    if not (
        isinstance(route, APIWebSocketRoute)
        and route.path == "/v1/stream/{symbol}"
    )
]


@app.websocket("/v1/stream/{symbol}")
async def shared_stream(
    websocket: WebSocket,
    symbol: str,
    timeframe: str = "5m",
    with_forecast: bool = False,
    advanced: bool = False,
    pred_len: int = 24,
) -> None:
    await websocket.accept()
    key = StreamKey(
        symbol=normalize_symbol(symbol),
        timeframe=timeframe,
        with_forecast=with_forecast,
        advanced=advanced,
        pred_len=max(1, int(pred_len)),
    )
    channel, queue = await _subscribe(key)
    try:
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        await _unsubscribe(channel, queue)
