from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import SUPPORTED_SYMBOLS, Settings
from .forecast import ForecastEngine, ForecastParameters
from .market import TIMEFRAMES, normalize_symbol
from .providers import MarketDataError


settings = Settings()
app = FastAPI(
    title="Traid Live Forecast API",
    version="0.2.0",
    description="Live quotes, completed candles, active candles, and Kronos projections.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=settings.cors_origins != ("*",),
    allow_methods=["*"],
    allow_headers=["*"],
)


class ForecastRequest(BaseModel):
    symbol: str = Field(examples=["XAUUSD"])
    timeframe: str = Field(default="5m", examples=["5m"])
    lookback: int = Field(default=400, ge=2, le=2048)
    pred_len: int = Field(default=24, ge=1, le=512)
    temperature: float = Field(default=1.0, gt=0, le=5)
    top_k: int = Field(default=0, ge=0)
    top_p: float = Field(default=0.9, gt=0, le=1)
    sample_count: int = Field(default=5, ge=1, le=100)


@lru_cache(maxsize=1)
def get_engine() -> ForecastEngine:
    return ForecastEngine(settings=settings)


def frame_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    copy = frame.copy()
    if "timestamp" in copy.columns:
        copy["timestamp"] = pd.to_datetime(copy["timestamp"], utc=True).map(
            lambda value: value.isoformat()
        )
    return copy.to_dict(orient="records")


def build_forecast_parameters(
    symbol: str,
    timeframe: str,
    pred_len: int,
) -> ForecastParameters:
    return ForecastParameters(
        symbol=symbol,
        timeframe=timeframe,
        lookback=settings.default_lookback,
        pred_len=pred_len,
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": settings.provider,
        "model": settings.model_id,
        "model_loaded": bool(get_engine.cache_info().currsize and get_engine()._predictor),
        "quote_poll_seconds": settings.quote_poll_seconds,
        "bar_poll_seconds": settings.bar_poll_seconds,
    }


@app.get("/v1/symbols")
def symbols() -> dict[str, Any]:
    return {
        "symbols": list(SUPPORTED_SYMBOLS),
        "timeframes": list(TIMEFRAMES),
        "provider": settings.provider,
        "aliases": settings.symbol_aliases() if settings.provider == "mt5" else None,
    }


@app.get("/v1/quote/{symbol}")
async def quote(symbol: str, timeframe: str = Query(default="5m")) -> dict[str, Any]:
    try:
        canonical = normalize_symbol(symbol)
        engine = get_engine()
        live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
        current_candle = await asyncio.to_thread(
            engine.provider.get_current_candle,
            canonical,
            timeframe,
        )
        return {
            "symbol": canonical,
            "timeframe": timeframe,
            "provider": engine.provider.name,
            "quote": live_quote.to_dict(),
            "current_candle": frame_records(current_candle)[0]
            if current_candle is not None and not current_candle.empty
            else None,
        }
    except (ValueError, MarketDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/candles/{symbol}")
async def candles(
    symbol: str,
    timeframe: str = Query(default="5m"),
    limit: int = Query(default=400, ge=2, le=5000),
) -> dict[str, Any]:
    try:
        canonical = normalize_symbol(symbol)
        frame = await asyncio.to_thread(
            get_engine().candles, canonical, timeframe, limit
        )
        return {
            "symbol": canonical,
            "timeframe": timeframe,
            "provider": get_engine().provider.name,
            "completed_only": True,
            "candles": frame_records(frame),
        }
    except (ValueError, MarketDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/forecast")
async def forecast(request: ForecastRequest) -> dict[str, Any]:
    try:
        params = ForecastParameters(
            symbol=request.symbol,
            timeframe=request.timeframe,
            lookback=request.lookback,
            pred_len=request.pred_len,
            temperature=request.temperature,
            top_k=request.top_k,
            top_p=request.top_p,
            sample_count=request.sample_count,
        )
        history, projection = await asyncio.to_thread(get_engine().forecast, params)
        engine = get_engine()
        live_quote = await asyncio.to_thread(
            engine.provider.get_quote,
            normalize_symbol(request.symbol),
        )
        current_candle = await asyncio.to_thread(
            engine.provider.get_current_candle,
            normalize_symbol(request.symbol),
            request.timeframe,
        )
        return {
            "symbol": normalize_symbol(request.symbol),
            "timeframe": request.timeframe,
            "provider": engine.provider.name,
            "model": settings.model_id,
            "completed_only": True,
            "parameters": request.model_dump(),
            "history": frame_records(history),
            "current_candle": frame_records(current_candle)[0]
            if current_candle is not None and not current_candle.empty
            else None,
            "quote": live_quote.to_dict(),
            "projection": frame_records(projection),
            "warning": "Probabilistic model output; not investment advice or an execution signal.",
        }
    except (ValueError, MarketDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Forecast failed: {exc}") from exc


@app.websocket("/v1/stream/{symbol}")
async def stream(
    websocket: WebSocket,
    symbol: str,
    timeframe: str = "5m",
    with_forecast: bool = False,
    pred_len: int = 24,
) -> None:
    await websocket.accept()
    forecast_task: asyncio.Task[tuple[pd.DataFrame, pd.DataFrame]] | None = None
    try:
        canonical = normalize_symbol(symbol)
        engine = get_engine()
        initial_frame = await asyncio.to_thread(engine.candles, canonical, timeframe, 2)
        last_completed = initial_frame["timestamp"].iloc[-1].isoformat()
        next_bar_check = 0.0
        quote_poll = settings.quote_poll_seconds
        if engine.provider.name == "massive":
            # Avoid hammering a REST snapshot endpoint. MT5 remains sub-second.
            quote_poll = max(quote_poll, 2.0)

        while True:
            loop_started = time.monotonic()
            try:
                live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
                current_candle = await asyncio.to_thread(
                    engine.provider.get_current_candle,
                    canonical,
                    timeframe,
                )
                payload: dict[str, Any] = {
                    "type": "market_update",
                    "symbol": canonical,
                    "timeframe": timeframe,
                    "quote": live_quote.to_dict(),
                    "current_candle": frame_records(current_candle)[0]
                    if current_candle is not None and not current_candle.empty
                    else None,
                    "server_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                }

                now = time.monotonic()
                if now >= next_bar_check:
                    latest_frame = await asyncio.to_thread(
                        engine.candles,
                        canonical,
                        timeframe,
                        2,
                    )
                    latest = latest_frame.tail(1)
                    latest_timestamp = latest["timestamp"].iloc[0].isoformat()
                    if latest_timestamp != last_completed:
                        payload["completed_candle"] = frame_records(latest)[0]
                        last_completed = latest_timestamp
                        if with_forecast and forecast_task is None:
                            params = build_forecast_parameters(
                                canonical,
                                timeframe,
                                pred_len,
                            )
                            forecast_task = asyncio.create_task(
                                asyncio.to_thread(engine.forecast, params)
                            )
                            payload["forecast_status"] = "refreshing"
                    next_bar_check = now + settings.bar_poll_seconds

                await websocket.send_json(payload)

                if forecast_task is not None and forecast_task.done():
                    try:
                        _, projection = forecast_task.result()
                        await websocket.send_json(
                            {
                                "type": "projection_update",
                                "symbol": canonical,
                                "timeframe": timeframe,
                                "projection": frame_records(projection),
                                "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                            }
                        )
                    except Exception as exc:
                        await websocket.send_json(
                            {"type": "forecast_error", "detail": str(exc)}
                        )
                    finally:
                        forecast_task = None
            except Exception as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})

            elapsed = time.monotonic() - loop_started
            await asyncio.sleep(max(0.05, quote_poll - elapsed))
    except WebSocketDisconnect:
        if forecast_task is not None:
            forecast_task.cancel()
        return
