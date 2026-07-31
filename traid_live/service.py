from __future__ import annotations

import asyncio
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
    version="0.1.0",
    description="Completed live candles and Kronos projections for metals and US indices.",
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


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    copy = frame.copy()
    if "timestamp" in copy.columns:
        copy["timestamp"] = pd.to_datetime(copy["timestamp"], utc=True).map(
            lambda value: value.isoformat()
        )
    return copy.to_dict(orient="records")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": settings.provider,
        "model": settings.model_id,
        "model_loaded": bool(get_engine.cache_info().currsize and get_engine()._predictor),
    }


@app.get("/v1/symbols")
def symbols() -> dict[str, Any]:
    return {
        "symbols": list(SUPPORTED_SYMBOLS),
        "timeframes": list(TIMEFRAMES),
        "provider": settings.provider,
        "aliases": settings.symbol_aliases() if settings.provider == "mt5" else None,
    }


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
        return {
            "symbol": normalize_symbol(request.symbol),
            "timeframe": request.timeframe,
            "provider": get_engine().provider.name,
            "model": settings.model_id,
            "completed_only": True,
            "parameters": request.model_dump(),
            "history": frame_records(history),
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
    try:
        canonical = normalize_symbol(symbol)
        last_sent: str | None = None
        while True:
            try:
                engine = get_engine()
                frame = await asyncio.to_thread(engine.candles, canonical, timeframe, 2)
                latest = frame.tail(1)
                latest_timestamp = latest["timestamp"].iloc[0].isoformat()

                if latest_timestamp != last_sent:
                    payload: dict[str, Any] = {
                        "type": "completed_candle",
                        "symbol": canonical,
                        "timeframe": timeframe,
                        "data": frame_records(latest)[0],
                    }
                    if with_forecast:
                        params = ForecastParameters(
                            symbol=canonical,
                            timeframe=timeframe,
                            lookback=settings.default_lookback,
                            pred_len=pred_len,
                        )
                        _, projection = await asyncio.to_thread(engine.forecast, params)
                        payload["projection"] = frame_records(projection)
                    await websocket.send_json(payload)
                    last_sent = latest_timestamp
                else:
                    await websocket.send_json(
                        {"type": "heartbeat", "last_completed": last_sent}
                    )
            except Exception as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})

            await asyncio.sleep(settings.stream_poll_seconds)
    except WebSocketDisconnect:
        return
