from __future__ import annotations

import asyncio
import logging
import secrets
import time
from functools import lru_cache
from typing import Any, Literal

import pandas as pd
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import SUPPORTED_SYMBOLS, Settings
from .forecast import ForecastEngine, ForecastParameters
from .market import TIMEFRAMES, normalize_symbol
from .providers import MarketDataError
from .providers.mt5 import MT5Provider
from .trading import (
    MT5TradeExecutor,
    MarketOrder,
    TradingError,
    TrailingStopSpec,
)


logger = logging.getLogger("traid")
settings = Settings()
app = FastAPI(
    title="Traid Live Forecast API",
    version="0.3.0",
    description=(
        "Live quotes, active/completed candles, Kronos projections, and guarded "
        "MetaTrader 5 order execution."
    ),
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


class MarketOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    volume: float = Field(gt=0)
    stop_loss_distance: float = Field(gt=0)
    take_profit_distance: float | None = Field(default=None, gt=0)
    trailing_distance: float | None = Field(default=None, gt=0)
    trailing_step: float = Field(default=0.0, ge=0)
    trailing_activation: float = Field(default=0.0, ge=0)
    deviation_points: int = Field(default=20, ge=0, le=10000)
    client_order_id: str | None = Field(default=None, max_length=100)
    confirm_live: bool = False


class ClosePositionRequest(BaseModel):
    volume: float | None = Field(default=None, gt=0)
    confirm_live: bool = False


class TrailingStopRequest(BaseModel):
    symbol: str
    distance: float = Field(gt=0)
    step: float = Field(default=0.0, ge=0)
    activation: float = Field(default=0.0, ge=0)


@lru_cache(maxsize=1)
def get_engine() -> ForecastEngine:
    return ForecastEngine(settings=settings)


@lru_cache(maxsize=1)
def get_trader() -> MT5TradeExecutor:
    engine = get_engine()
    if not isinstance(engine.provider, MT5Provider):
        raise TradingError("Trade execution requires the MT5 provider.")
    return MT5TradeExecutor(settings=settings, provider=engine.provider)


def require_trading_key(
    x_traid_key: str | None = Header(default=None, alias="X-Traid-Key"),
) -> None:
    expected = settings.trading_api_key
    if not expected or not x_traid_key or not secrets.compare_digest(x_traid_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing trading API key.")


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


async def trailing_worker() -> None:
    while True:
        try:
            updates = await asyncio.to_thread(get_trader().process_trailing_once)
            if updates:
                logger.info("Trailing-stop updates: %s", updates)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Trailing-stop worker iteration failed")
        await asyncio.sleep(settings.trailing_poll_seconds)


@app.on_event("startup")
async def start_background_workers() -> None:
    settings.validate()
    if settings.trading_enabled and settings.provider == "mt5":
        app.state.trailing_task = asyncio.create_task(trailing_worker())


@app.on_event("shutdown")
async def stop_background_workers() -> None:
    task = getattr(app.state, "trailing_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": settings.provider,
        "model": settings.model_id,
        "model_loaded": bool(get_engine.cache_info().currsize and get_engine()._predictor),
        "quote_poll_seconds": settings.quote_poll_seconds,
        "bar_poll_seconds": settings.bar_poll_seconds,
        "trading_enabled": settings.trading_enabled,
        "trading_mode": settings.trading_mode,
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
        canonical = normalize_symbol(request.symbol)
        live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
        current_candle = await asyncio.to_thread(
            engine.provider.get_current_candle,
            canonical,
            request.timeframe,
        )
        return {
            "symbol": canonical,
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


@app.get("/v1/trading/status", dependencies=[Depends(require_trading_key)])
async def trading_status() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_trader().status)
    except TradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/trading/positions", dependencies=[Depends(require_trading_key)])
async def trading_positions() -> dict[str, Any]:
    try:
        return {"positions": await asyncio.to_thread(get_trader().positions)}
    except TradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/trading/orders", dependencies=[Depends(require_trading_key)])
async def place_order(request: MarketOrderRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            get_trader().place_market_order,
            MarketOrder(**request.model_dump()),
        )
        return result
    except (TradingError, ValueError, MarketDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/v1/trading/positions/{position_ticket}/close",
    dependencies=[Depends(require_trading_key)],
)
async def close_position(
    position_ticket: int,
    request: ClosePositionRequest,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            get_trader().close_position,
            position_ticket,
            request.volume,
            request.confirm_live,
        )
    except TradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put(
    "/v1/trading/positions/{position_ticket}/trailing",
    dependencies=[Depends(require_trading_key)],
)
async def configure_trailing(
    position_ticket: int,
    request: TrailingStopRequest,
) -> dict[str, Any]:
    try:
        positions = await asyncio.to_thread(get_trader().positions)
        position = next(
            (item for item in positions if item["ticket"] == position_ticket),
            None,
        )
        if position is None:
            raise TradingError(f"Position {position_ticket} was not found.")
        spec = TrailingStopSpec(
            position_ticket=position_ticket,
            symbol=request.symbol,
            side=position["side"],
            distance=request.distance,
            step=request.step,
            activation=request.activation,
        )
        return await asyncio.to_thread(get_trader().configure_trailing, spec)
    except TradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/v1/trading/positions/{position_ticket}/trailing",
    dependencies=[Depends(require_trading_key)],
)
async def disable_trailing(position_ticket: int) -> dict[str, Any]:
    return {
        "position_ticket": position_ticket,
        "disabled": await asyncio.to_thread(
            get_trader().disable_trailing,
            position_ticket,
        ),
    }


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
