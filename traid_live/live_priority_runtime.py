from __future__ import annotations

import asyncio
import time
from typing import Any

import pandas as pd

from .forecast import ForecastParameters
from .market import get_timeframe
from .service import frame_records, get_engine, get_platform, settings, store, utc_now_iso
from .service_patch import ADVANCED_PATH_COUNT, NORMAL_SAMPLE_COUNT
from . import multitab_runtime as runtime


# Keep UI movement responsive without creating an excessive number of MT5 calls.
# Forecast inference, scoring, and candle-boundary work run in separate tasks.
_LIVE_POLL_SECONDS = max(0.1, min(float(settings.quote_poll_seconds), 0.25))


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _record_from_frame(frame: pd.DataFrame | None) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        return None
    records = frame_records(frame)
    return dict(records[-1]) if records else None


def _fallback_candle(quote: Any, timeframe: str) -> dict[str, Any]:
    seconds = get_timeframe(timeframe).seconds
    timestamp = pd.Timestamp(quote.timestamp)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    bucket_seconds = int(timestamp.timestamp()) // seconds * seconds
    candle_timestamp = pd.Timestamp(bucket_seconds, unit="s", tz="UTC")
    price = float(quote.price)
    return {
        "timestamp": candle_timestamp.isoformat(),
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 0.0,
        "amount": 0.0,
    }


def _apply_quote(
    candle: dict[str, Any] | None,
    quote: Any,
    timeframe: str,
) -> dict[str, Any]:
    expected = _fallback_candle(quote, timeframe)
    try:
        same_bucket = (
            candle is not None
            and _timestamp(candle["timestamp"]) == _timestamp(expected["timestamp"])
        )
    except Exception:
        same_bucket = False

    # A quote in a new timeframe bucket must begin a brand-new live candle
    # immediately. Do not wait for the slower completed-bar worker to notice it.
    row = dict(candle) if same_bucket and candle is not None else expected
    price = float(quote.price)
    opening = float(row.get("open", price))
    previous_high = float(row.get("high", max(opening, price)))
    previous_low = float(row.get("low", min(opening, price)))
    row["open"] = opening
    row["close"] = price
    row["high"] = max(previous_high, opening, price)
    row["low"] = min(previous_low, opening, price)
    row.setdefault("volume", 0.0)
    row.setdefault("amount", 0.0)
    return row


def _merge_snapshot(
    snapshot: dict[str, Any] | None,
    live: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return live
    if live is None:
        return snapshot
    try:
        same_candle = _timestamp(snapshot["timestamp"]) == _timestamp(live["timestamp"])
    except Exception:
        same_candle = False
    if not same_candle:
        return snapshot

    merged = dict(snapshot)
    live_close = float(live["close"])
    merged["close"] = live_close
    merged["high"] = max(float(snapshot["high"]), float(live["high"]), live_close)
    merged["low"] = min(float(snapshot["low"]), float(live["low"]), live_close)
    return merged


async def _live_priority_channel(channel: runtime.SharedChannel) -> None:
    key = channel.key
    engine = get_engine()
    live_candle: dict[str, Any] | None = None
    last_completed: str | None = None
    refresh_queued = False
    forecast_cycle_task: asyncio.Task[None] | None = None
    score_task: asyncio.Task[int] | None = None

    async def generate_forecast_cycle() -> None:
        nonlocal refresh_queued
        while True:
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
            try:
                result = await asyncio.to_thread(
                    get_platform().generate,
                    params,
                    advanced=key.advanced,
                    paths=path_count,
                )
                result.update(
                    {
                        "type": "projection_update",
                        "accuracy": store.accuracy(key.symbol, key.timeframe),
                    }
                )
                await runtime._broadcast(channel, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await runtime._broadcast(
                    channel,
                    {"type": "forecast_error", "detail": str(exc)},
                )

            if not refresh_queued:
                break
            refresh_queued = False
            await runtime._broadcast(
                channel,
                {"type": "forecast_status", "status": "refreshing_queued"},
            )

    async def candle_and_forecast_worker() -> None:
        nonlocal live_candle, last_completed, forecast_cycle_task, refresh_queued, score_task
        while True:
            started = time.monotonic()
            try:
                snapshot_frame = await asyncio.to_thread(
                    engine.provider.get_current_candle,
                    key.symbol,
                    key.timeframe,
                )
                live_candle = _merge_snapshot(
                    _record_from_frame(snapshot_frame),
                    live_candle,
                )

                latest_frame = await asyncio.to_thread(
                    engine.candles,
                    key.symbol,
                    key.timeframe,
                    2,
                )
                latest = latest_frame.tail(1)
                latest_timestamp = _timestamp(latest["timestamp"].iloc[0])
                if last_completed is None:
                    last_completed = latest_timestamp
                elif latest_timestamp != last_completed:
                    last_completed = latest_timestamp
                    completed = frame_records(latest)[0]
                    await runtime._broadcast(
                        channel,
                        {
                            "type": "market_update",
                            "symbol": key.symbol,
                            "timeframe": key.timeframe,
                            "completed_candle": completed,
                            "current_candle": live_candle,
                            "server_timestamp": utc_now_iso(),
                        },
                    )

                    if score_task is None or score_task.done():
                        score_task = asyncio.create_task(
                            asyncio.to_thread(
                                get_platform().score_market,
                                key.symbol,
                                key.timeframe,
                            )
                        )

                    if key.with_forecast:
                        if forecast_cycle_task is None or forecast_cycle_task.done():
                            forecast_cycle_task = asyncio.create_task(
                                generate_forecast_cycle()
                            )
                            await runtime._broadcast(
                                channel,
                                {"type": "forecast_status", "status": "refreshing"},
                            )
                        else:
                            refresh_queued = True
                            await runtime._broadcast(
                                channel,
                                {"type": "forecast_status", "status": "queued"},
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await runtime._broadcast(
                    channel,
                    {"type": "error", "detail": f"Candle worker recovering: {exc}"},
                )

            await asyncio.sleep(
                max(0.05, float(settings.bar_poll_seconds) - (time.monotonic() - started))
            )

    try:
        initial = await asyncio.to_thread(
            engine.provider.get_current_candle,
            key.symbol,
            key.timeframe,
        )
        live_candle = _record_from_frame(initial)
        completed = await asyncio.to_thread(
            engine.candles,
            key.symbol,
            key.timeframe,
            2,
        )
        last_completed = _timestamp(completed["timestamp"].iloc[-1])

        worker_task = asyncio.create_task(candle_and_forecast_worker())
        try:
            while True:
                started = time.monotonic()
                try:
                    quote = await asyncio.to_thread(
                        engine.provider.get_quote,
                        key.symbol,
                    )
                    # The candle close sent to the browser is always the exact quote
                    # price from this same payload. Forecast work cannot alter it.
                    live_candle = _apply_quote(live_candle, quote, key.timeframe)
                    await runtime._broadcast(
                        channel,
                        {
                            "type": "market_update",
                            "symbol": key.symbol,
                            "timeframe": key.timeframe,
                            "quote": quote.to_dict(),
                            "current_candle": dict(live_candle),
                            "server_timestamp": utc_now_iso(),
                            "live_priority": True,
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await runtime._broadcast(
                        channel,
                        {"type": "error", "detail": f"Live quote recovering: {exc}"},
                    )

                await asyncio.sleep(
                    max(0.02, _LIVE_POLL_SECONDS - (time.monotonic() - started))
                )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
    finally:
        for task in (forecast_cycle_task, score_task):
            if task is not None and not task.done():
                task.cancel()


async def resilient_live_priority_channel(channel: runtime.SharedChannel) -> None:
    while channel.subscribers:
        try:
            await _live_priority_channel(channel)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await runtime._broadcast(
                channel,
                {"type": "error", "detail": f"Live stream recovering: {exc}"},
            )
            await asyncio.sleep(0.5)


# _subscribe resolves this global when creating each channel, so replacing it here
# makes every channel created after server startup use the live-priority loop.
runtime._run_channel = resilient_live_priority_channel
