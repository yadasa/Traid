from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from functools import lru_cache
from typing import Any, Literal

import httpx
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from .advanced_trading import AdvancedMT5Trader, PendingOrder, SmartTrailing
from .auth import AUTH, LIMITER, Principal, client_key
from .config import SUPPORTED_SYMBOLS, Settings
from .forecast import ForecastEngine, ForecastParameters
from .market import TIMEFRAMES, normalize_symbol
from .platform import ForecastPlatform, PlatformStore, RiskEngine, utc_now_iso
from .providers import MarketDataError
from .providers.mt5 import MT5Provider
from .trading import MT5TradeExecutor, MarketOrder, TradingError, TrailingStopSpec


logger = logging.getLogger("traid")
settings = Settings()
store = PlatformStore(settings.database_path)
app = FastAPI(
    title="Traid Forecast Overlay Trading API",
    version="1.0.0",
    description="Live broker data, persistent Kronos forecasts, uncertainty, replay, risk controls, journaling, and guarded MT5 execution.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials="*" not in settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class ForecastRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    lookback: int = Field(default=400, ge=2, le=2048)
    pred_len: int = Field(default=24, ge=1, le=512)
    temperature: float = Field(default=1.0, gt=0, le=5)
    top_k: int = Field(default=0, ge=0)
    top_p: float = Field(default=0.9, gt=0, le=1)
    sample_count: int = Field(default=5, ge=1, le=100)
    advanced: bool = False
    uncertainty_paths: int | None = Field(default=None, ge=3, le=25)


class MarketOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    volume: float | None = Field(default=None, gt=0)
    risk_percent: float | None = Field(default=None, gt=0, le=10)
    stop_loss_distance: float = Field(gt=0)
    take_profit_distance: float | None = Field(default=None, gt=0)
    trailing_distance: float | None = Field(default=None, gt=0)
    trailing_step: float = Field(default=0.0, ge=0)
    trailing_activation: float = Field(default=0.0, ge=0)
    deviation_points: int = Field(default=20, ge=0, le=10000)
    client_order_id: str | None = Field(default=None, max_length=100)
    forecast_id: str | None = None
    entry_reason: str | None = Field(default=None, max_length=2000)
    confirm_live: bool = False

    @model_validator(mode="after")
    def require_size(self):
        if self.volume is None and self.risk_percent is None:
            raise ValueError("Provide volume or risk_percent.")
        return self


class PendingOrderRequest(BaseModel):
    symbol: str
    kind: Literal["buy_limit", "sell_limit", "buy_stop", "sell_stop"]
    volume: float = Field(gt=0)
    price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    expiration: int | None = None
    deviation_points: int = Field(default=20, ge=0, le=10000)
    client_order_id: str | None = Field(default=None, max_length=100)
    confirm_live: bool = False


class OCORequest(BaseModel):
    first: PendingOrderRequest
    second: PendingOrderRequest


class ClosePositionRequest(BaseModel):
    volume: float | None = Field(default=None, gt=0)
    confirm_live: bool = False
    exit_reason: str | None = Field(default=None, max_length=2000)


class ModifyPositionRequest(BaseModel):
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    confirm_live: bool = False


class BreakEvenRequest(BaseModel):
    offset: float = 0.0
    confirm_live: bool = False


class TrailingStopRequest(BaseModel):
    symbol: str
    distance: float = Field(gt=0)
    step: float = Field(default=0.0, ge=0)
    activation: float = Field(default=0.0, ge=0)


class SmartTrailingRequest(BaseModel):
    kind: Literal["fixed", "percent", "atr", "candle"] = "fixed"
    value: float = Field(gt=0)
    activation: float = Field(default=0, ge=0)
    step: float = Field(default=0, ge=0)
    timeframe: str = "5m"
    lookback: int = Field(default=14, ge=1, le=500)


class RiskSizeRequest(BaseModel):
    symbol: str
    stop_distance: float = Field(gt=0)
    risk_percent: float = Field(default=0.5, gt=0, le=10)


class JournalCreateRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    position_ticket: int | None = None
    order_ticket: int | None = None
    status: str = "open"
    entry_price: float | None = None
    exit_price: float | None = None
    volume: float | None = None
    risk_amount: float | None = None
    forecast_id: str | None = None
    entry_reason: str | None = None
    exit_reason: str | None = None
    notes: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    pnl: float | None = None
    mfe: float | None = None
    mae: float | None = None


class JournalPatchRequest(BaseModel):
    position_ticket: int | None = None
    order_ticket: int | None = None
    status: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    volume: float | None = None
    risk_amount: float | None = None
    forecast_id: str | None = None
    entry_reason: str | None = None
    exit_reason: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    pnl: float | None = None
    mfe: float | None = None
    mae: float | None = None


class EventRequest(BaseModel):
    id: str | None = None
    starts_at: str
    currency: str = "USD"
    impact: Literal["low", "medium", "high"] = "medium"
    title: str
    source: str | None = None
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    metadata: dict[str, Any] = {}


class SettingRequest(BaseModel):
    value: Any


class ReplayRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    start_index: int = Field(default=400, ge=30, le=4500)
    steps: int = Field(default=100, ge=1, le=1000)
    pred_len: int = Field(default=12, ge=1, le=200)


@lru_cache(maxsize=1)
def get_engine() -> ForecastEngine:
    return ForecastEngine(settings=settings)


@lru_cache(maxsize=1)
def get_platform() -> ForecastPlatform:
    return ForecastPlatform(get_engine(), store)


@lru_cache(maxsize=1)
def get_risk() -> RiskEngine:
    return RiskEngine(store)


@lru_cache(maxsize=1)
def get_trader() -> MT5TradeExecutor:
    engine = get_engine()
    if not isinstance(engine.provider, MT5Provider):
        raise TradingError("Trade execution requires the MT5 provider.")
    return MT5TradeExecutor(settings=settings, provider=engine.provider)


@lru_cache(maxsize=1)
def get_advanced_trader() -> AdvancedMT5Trader:
    return AdvancedMT5Trader(get_trader(), store)


def frame_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    copy = frame.copy()
    if "timestamp" in copy.columns:
        copy["timestamp"] = pd.to_datetime(copy["timestamp"], utc=True).map(lambda value: value.isoformat())
    return copy.to_dict(orient="records")


def params_from_request(request: ForecastRequest) -> ForecastParameters:
    return ForecastParameters(
        symbol=request.symbol, timeframe=request.timeframe, lookback=request.lookback,
        pred_len=request.pred_len, temperature=request.temperature, top_k=request.top_k,
        top_p=request.top_p, sample_count=request.sample_count,
    )


def principal_name(principal: Principal) -> str:
    return f"{principal.name}:{principal.role}"


def trading_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def open_risk_amount(trader: MT5TradeExecutor) -> float:
    total = 0.0
    for position in trader.positions():
        stop = float(position.get("stop_loss") or 0)
        if not stop:
            continue
        info = trader.mt5.symbol_info(position["broker_symbol"])
        if info is None:
            continue
        tick_size = float(getattr(info, "trade_tick_size", 0) or info.point)
        tick_value = float(getattr(info, "trade_tick_value_loss", 0) or getattr(info, "trade_tick_value", 0) or 0)
        if tick_size > 0 and tick_value > 0:
            total += abs(float(position["open_price"]) - stop) / tick_size * tick_value * float(position["volume"])
    return total


async def risk_status() -> dict[str, Any]:
    trader = get_trader()
    account = trader.mt5.account_info()
    if account is None:
        raise TradingError("MT5 account information is unavailable.")
    return get_risk().status(float(account.equity), open_risk_amount(trader))


async def calendar_refresh_once() -> int:
    if not settings.calendar_url:
        return 0
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(settings.calendar_url)
        response.raise_for_status()
        payload = response.json()
    events = payload.get("events", payload) if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise ValueError("TRAID_CALENDAR_URL must return a JSON list or an object containing events.")
    for event in events:
        store.upsert_event(event, actor="calendar-sync")
    return len(events)


async def background_worker() -> None:
    next_calendar = 0.0
    while True:
        try:
            if settings.trading_enabled and settings.trading_mode == "live" and settings.provider == "mt5":
                await asyncio.to_thread(get_advanced_trader().process_smart_trailing_once)
                await asyncio.to_thread(get_advanced_trader().reconcile_oco)
            if settings.calendar_url and time.monotonic() >= next_calendar:
                try:
                    count = await calendar_refresh_once()
                    logger.info("Calendar refresh imported %s events", count)
                finally:
                    next_calendar = time.monotonic() + settings.calendar_refresh_minutes * 60
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Traid background worker iteration failed")
        await asyncio.sleep(settings.trailing_poll_seconds if settings.trading_mode == "live" else 5)


@app.on_event("startup")
async def startup() -> None:
    settings.validate()
    store.set_setting("advanced_forecast", store.get_setting("advanced_forecast", settings.advanced_forecast_default))
    store.set_setting("uncertainty_paths", store.get_setting("uncertainty_paths", settings.uncertainty_paths))
    app.state.worker = asyncio.create_task(background_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "worker", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.post("/v1/auth/login")
async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    LIMITER.check(client_key(request, "login"), limit=10, window_seconds=300)
    result = AUTH.login(payload.username, payload.password)
    store.audit("auth.login", actor=payload.username)
    return result


@app.post("/v1/auth/logout")
async def logout(request: Request, principal: Principal = Depends(AUTH.require("viewer"))) -> dict[str, bool]:
    AUTH.logout(request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
    store.audit("auth.logout", actor=principal_name(principal))
    return {"logged_out": True}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok", "version": app.version, "provider": settings.provider,
        "model": settings.model_id, "model_loaded": bool(get_engine.cache_info().currsize and get_engine()._predictor),
        "database": str(store.path), "trading_enabled": settings.trading_enabled,
        "trading_mode": settings.trading_mode, "advanced_forecast": store.get_setting("advanced_forecast", False),
    }


@app.get("/v1/symbols")
def symbols() -> dict[str, Any]:
    return {"symbols": list(SUPPORTED_SYMBOLS), "timeframes": list(TIMEFRAMES), "provider": settings.provider, "aliases": settings.symbol_aliases() if settings.provider == "mt5" else None}


@app.get("/v1/quote/{symbol}")
async def quote(symbol: str, timeframe: str = "5m") -> dict[str, Any]:
    try:
        canonical = normalize_symbol(symbol); engine = get_engine()
        live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
        current = await asyncio.to_thread(engine.provider.get_current_candle, canonical, timeframe)
        return {"symbol": canonical, "timeframe": timeframe, "provider": engine.provider.name, "quote": live_quote.to_dict(), "current_candle": frame_records(current)[0] if current is not None and not current.empty else None}
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc


@app.get("/v1/candles/{symbol}")
async def candles(symbol: str, timeframe: str = "5m", limit: int = Query(400, ge=2, le=5000)) -> dict[str, Any]:
    try:
        canonical = normalize_symbol(symbol)
        frame = await asyncio.to_thread(get_engine().candles, canonical, timeframe, limit)
        return {"symbol": canonical, "timeframe": timeframe, "provider": get_engine().provider.name, "completed_only": True, "candles": frame_records(frame)}
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc


@app.post("/v1/forecast")
async def forecast(request: ForecastRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(get_platform().generate, params_from_request(request), advanced=request.advanced, paths=request.uncertainty_paths)
        engine = get_engine(); canonical = normalize_symbol(request.symbol)
        live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
        current = await asyncio.to_thread(engine.provider.get_current_candle, canonical, request.timeframe)
        result.update({"provider": engine.provider.name, "model": settings.model_id, "quote": live_quote.to_dict(), "current_candle": frame_records(current)[0] if current is not None and not current.empty else None, "accuracy": store.accuracy(canonical, request.timeframe), "warning": "Probabilistic model output; not investment advice or an autonomous execution signal."})
        return result
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc
    except Exception as exc:
        logger.exception("Forecast failed")
        raise HTTPException(status_code=500, detail=f"Forecast failed: {exc}") from exc


@app.get("/v1/forecasts/{symbol}")
async def forecast_history(symbol: str, timeframe: str = "5m", limit: int = Query(25, ge=1, le=500)) -> dict[str, Any]:
    return {"forecasts": store.forecasts(symbol, timeframe, limit), "accuracy": store.accuracy(symbol, timeframe)}


@app.get("/v1/forecasts/id/{forecast_id}")
async def forecast_by_id(forecast_id: str) -> dict[str, Any]:
    item = store.forecast(forecast_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Forecast not found.")
    return item


@app.post("/v1/forecasts/{symbol}/score")
async def score_forecasts(symbol: str, timeframe: str = "5m", principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    count = await asyncio.to_thread(get_platform().score_market, symbol, timeframe)
    store.audit("forecast.scored", actor=principal_name(principal), payload={"symbol": symbol, "timeframe": timeframe, "new_scores": count})
    return {"new_scores": count, "accuracy": store.accuracy(symbol, timeframe)}


@app.get("/v1/forecast-context/{symbol}")
async def forecast_context(symbol: str, timeframe: str = "5m") -> dict[str, Any]:
    return {"multi_timeframe": await asyncio.to_thread(get_platform().consensus, symbol, timeframe), "cross_market": await asyncio.to_thread(get_platform().cross_market_context)}


@app.get("/v1/platform/settings")
async def platform_settings() -> dict[str, Any]:
    return store.settings()


@app.put("/v1/platform/settings/{key}")
async def update_setting(key: str, payload: SettingRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    supported = set(store.settings()) | {"advanced_forecast", "uncertainty_paths"}
    if key not in supported and not key.startswith("smart_trailing:"):
        raise HTTPException(status_code=400, detail="Unknown platform setting.")
    return {"key": key, "value": store.set_setting(key, payload.value, principal_name(principal))}


@app.get("/v1/calendar")
async def calendar(start: str | None = None, end: str | None = None, impact: str | None = None) -> dict[str, Any]:
    return {"events": store.events(start, end, impact), "source_configured": bool(settings.calendar_url)}


@app.post("/v1/calendar")
async def add_event(payload: EventRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    return store.upsert_event(payload.model_dump(exclude_none=True), principal_name(principal))


@app.post("/v1/calendar/refresh")
async def refresh_calendar(principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, int]:
    if not settings.calendar_url:
        raise HTTPException(status_code=400, detail="TRAID_CALENDAR_URL is not configured.")
    return {"imported": await calendar_refresh_once()}


@app.post("/v1/replay")
async def replay(payload: ReplayRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_platform().replay, payload.symbol, payload.timeframe, payload.start_index, payload.steps, payload.pred_len)
    except (ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc


@app.get("/v1/journal")
async def journal(limit: int = Query(200, ge=1, le=2000), principal: Principal = Depends(AUTH.require("viewer"))) -> dict[str, Any]:
    return {"entries": store.journal_entries(limit)}


@app.post("/v1/journal")
async def journal_create(payload: JournalCreateRequest, principal: Principal = Depends(AUTH.require("trader"))) -> dict[str, Any]:
    return store.journal_create(payload.model_dump(), principal_name(principal))


@app.patch("/v1/journal/{journal_id}")
async def journal_update(journal_id: str, payload: JournalPatchRequest, principal: Principal = Depends(AUTH.require("trader"))) -> dict[str, Any]:
    try:
        return store.journal_update(journal_id, payload.model_dump(exclude_none=True), principal_name(principal))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Journal entry not found.") from exc


@app.get("/v1/audit")
async def audit(limit: int = Query(200, ge=1, le=2000), principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    return {"entries": store.audit_entries(limit)}


@app.get("/v1/trading/status")
async def trading_status(principal: Principal = Depends(AUTH.require("viewer"))) -> dict[str, Any]:
    try:
        status = await asyncio.to_thread(get_trader().status)
        status["risk"] = await risk_status()
        status["pending_orders"] = await asyncio.to_thread(get_advanced_trader().pending_orders)
        return status
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.get("/v1/trading/positions")
async def trading_positions(principal: Principal = Depends(AUTH.require("viewer"))) -> dict[str, Any]:
    try:
        return {"positions": await asyncio.to_thread(get_trader().positions), "pending_orders": await asyncio.to_thread(get_advanced_trader().pending_orders)}
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/risk-size")
async def calculate_risk_size(payload: RiskSizeRequest, principal: Principal = Depends(AUTH.require("trader"))) -> dict[str, Any]:
    try:
        trader = get_trader(); canonical, broker_symbol = trader.provider._broker_symbol(payload.symbol)
        account = trader.mt5.account_info(); info = trader.mt5.symbol_info(broker_symbol)
        if account is None or info is None:
            raise TradingError("MT5 account or symbol information is unavailable.")
        result = get_risk().position_size(
            equity=float(account.equity), risk_percent=payload.risk_percent,
            stop_distance=payload.stop_distance,
            tick_size=float(getattr(info, "trade_tick_size", 0) or info.point),
            tick_value=float(getattr(info, "trade_tick_value_loss", 0) or getattr(info, "trade_tick_value", 0)),
            volume_min=float(info.volume_min), volume_max=min(float(info.volume_max), settings.max_order_lots),
            volume_step=float(info.volume_step),
        )
        return {"symbol": canonical, "equity": float(account.equity), **result, "risk": await risk_status()}
    except (TradingError, ValueError) as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/orders")
async def place_order(payload: MarketOrderRequest, request: Request, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    LIMITER.check(client_key(request, "order"), limit=30, window_seconds=60)
    try:
        risk = await risk_status()
        if not risk["allowed"]:
            raise TradingError("Risk engine blocked the order: " + "; ".join(risk["reasons"]))
        trader = get_trader(); volume = payload.volume; risk_amount = None
        if payload.risk_percent is not None:
            size = await calculate_risk_size(RiskSizeRequest(symbol=payload.symbol, stop_distance=payload.stop_loss_distance, risk_percent=payload.risk_percent), principal)
            volume = float(size["volume"]); risk_amount = float(size["estimated_loss"])
        assert volume is not None
        client_order_id = payload.client_order_id or __import__("uuid").uuid4().hex
        remembered = store.idempotent_response(client_order_id)
        if remembered:
            return remembered
        result = await asyncio.to_thread(
            trader.place_market_order,
            MarketOrder(
                symbol=payload.symbol, side=payload.side, volume=volume,
                stop_loss_distance=payload.stop_loss_distance,
                take_profit_distance=payload.take_profit_distance,
                trailing_distance=payload.trailing_distance, trailing_step=payload.trailing_step,
                trailing_activation=payload.trailing_activation, deviation_points=payload.deviation_points,
                client_order_id=client_order_id, confirm_live=payload.confirm_live,
            ),
        )
        store.remember_order(client_order_id, result)
        entry = store.journal_create({
            "symbol": payload.symbol, "side": payload.side,
            "position_ticket": result.get("position_ticket"), "order_ticket": result.get("result", {}).get("order") if isinstance(result.get("result"), dict) else None,
            "status": "paper" if result.get("paper") else "open", "entry_price": result.get("fill_price") or result.get("requested_price"),
            "volume": volume, "risk_amount": risk_amount, "forecast_id": payload.forecast_id,
            "entry_reason": payload.entry_reason, "metadata": {"order": result, "risk": risk},
        }, principal_name(principal))
        result["journal_id"] = entry["id"]
        store.audit("order.market", actor=principal_name(principal), entity_type="position", entity_id=result.get("position_ticket"), payload=result)
        return result
    except (TradingError, ValueError, MarketDataError) as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/pending")
async def place_pending(payload: PendingOrderRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        risk = await risk_status()
        if not risk["allowed"]:
            raise TradingError("Risk engine blocked the order: " + "; ".join(risk["reasons"]))
        result = await asyncio.to_thread(get_advanced_trader().place_pending, PendingOrder(**payload.model_dump()))
        store.audit("order.pending.request", actor=principal_name(principal), entity_type="order", entity_id=result.get("order_ticket"), payload=result)
        return result
    except (TradingError, ValueError) as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/oco")
async def place_oco(payload: OCORequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_advanced_trader().create_oco, PendingOrder(**payload.first.model_dump()), PendingOrder(**payload.second.model_dump()))
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.delete("/v1/trading/pending/{ticket}")
async def cancel_pending(ticket: int, confirm_live: bool = False, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_advanced_trader().cancel_pending, ticket, confirm_live)
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/positions/{ticket}/close")
async def close_position(ticket: int, payload: ClosePositionRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        positions = await asyncio.to_thread(get_trader().positions)
        before = next((item for item in positions if item["ticket"] == ticket), None)
        result = await asyncio.to_thread(get_trader().close_position, ticket, payload.volume, payload.confirm_live)
        if before:
            for entry in store.journal_entries(2000):
                if entry.get("position_ticket") == ticket and entry.get("status") == "open":
                    store.journal_update(entry["id"], {"status": "closed", "exit_price": before["current_price"], "exit_reason": payload.exit_reason, "pnl": before["profit"]}, principal_name(principal))
                    break
        return result
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/positions/close-all")
async def close_all(symbol: str | None = None, confirm_live: bool = False, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return {"results": await asyncio.to_thread(get_advanced_trader().close_all, confirm_live, symbol)}
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.put("/v1/trading/positions/{ticket}")
async def modify_position(ticket: int, payload: ModifyPositionRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_advanced_trader().modify_position, ticket, stop_loss=payload.stop_loss, take_profit=payload.take_profit, confirm_live=payload.confirm_live)
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.post("/v1/trading/positions/{ticket}/break-even")
async def break_even(ticket: int, payload: BreakEvenRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_advanced_trader().move_to_break_even, ticket, payload.offset, payload.confirm_live)
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.put("/v1/trading/positions/{ticket}/trailing")
async def configure_trailing(ticket: int, payload: TrailingStopRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        positions = await asyncio.to_thread(get_trader().positions)
        position = next((item for item in positions if item["ticket"] == ticket), None)
        if not position:
            raise TradingError(f"Position {ticket} was not found.")
        return await asyncio.to_thread(get_trader().configure_trailing, TrailingStopSpec(position_ticket=ticket, symbol=payload.symbol, side=position["side"], distance=payload.distance, step=payload.step, activation=payload.activation))
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.put("/v1/trading/positions/{ticket}/smart-trailing")
async def configure_smart_trailing(ticket: int, payload: SmartTrailingRequest, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_advanced_trader().configure_smart_trailing, SmartTrailing(position_ticket=ticket, **payload.model_dump()))
    except TradingError as exc:
        raise trading_error(exc) from exc


@app.delete("/v1/trading/positions/{ticket}/trailing")
async def disable_trailing(ticket: int, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    store.set_setting(f"smart_trailing:{ticket}", None, principal_name(principal))
    return {"position_ticket": ticket, "disabled": await asyncio.to_thread(get_trader().disable_trailing, ticket)}


@app.post("/v1/trading/emergency-stop")
async def emergency_stop(close_positions: bool = False, confirm_live: bool = False, principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, Any]:
    store.set_setting("trading_disabled", True, principal_name(principal))
    results = await asyncio.to_thread(get_advanced_trader().close_all, confirm_live, None) if close_positions else []
    store.audit("trading.emergency_stop", actor=principal_name(principal), payload={"close_positions": close_positions, "results": results})
    return {"trading_disabled": True, "closed": results}


@app.post("/v1/trading/emergency-resume")
async def emergency_resume(principal: Principal = Depends(AUTH.require("admin"))) -> dict[str, bool]:
    store.set_setting("trading_disabled", False, principal_name(principal))
    return {"trading_disabled": False}


@app.websocket("/v1/stream/{symbol}")
async def stream(websocket: WebSocket, symbol: str, timeframe: str = "5m", with_forecast: bool = False, advanced: bool = False, pred_len: int = 24) -> None:
    await websocket.accept()
    forecast_task: asyncio.Task[dict[str, Any]] | None = None
    refresh_queued = False
    try:
        canonical = normalize_symbol(symbol); engine = get_engine()
        initial = await asyncio.to_thread(engine.candles, canonical, timeframe, 2)
        last_completed = initial["timestamp"].iloc[-1].isoformat()
        next_bar_check = 0.0
        quote_poll = max(settings.quote_poll_seconds, 2.0) if engine.provider.name == "massive" else settings.quote_poll_seconds

        async def begin_forecast() -> asyncio.Task[dict[str, Any]]:
            request = ForecastRequest(symbol=canonical, timeframe=timeframe, lookback=settings.default_lookback, pred_len=pred_len, advanced=advanced, uncertainty_paths=store.get_setting("uncertainty_paths", settings.uncertainty_paths) if advanced else None)
            return asyncio.create_task(asyncio.to_thread(get_platform().generate, params_from_request(request), advanced=advanced, paths=request.uncertainty_paths))

        while True:
            loop_started = time.monotonic()
            try:
                live_quote = await asyncio.to_thread(engine.provider.get_quote, canonical)
                current = await asyncio.to_thread(engine.provider.get_current_candle, canonical, timeframe)
                payload: dict[str, Any] = {"type": "market_update", "symbol": canonical, "timeframe": timeframe, "quote": live_quote.to_dict(), "current_candle": frame_records(current)[0] if current is not None and not current.empty else None, "server_timestamp": utc_now_iso()}
                now = time.monotonic()
                if now >= next_bar_check:
                    latest_frame = await asyncio.to_thread(engine.candles, canonical, timeframe, 2)
                    latest = latest_frame.tail(1); latest_timestamp = latest["timestamp"].iloc[0].isoformat()
                    if latest_timestamp != last_completed:
                        payload["completed_candle"] = frame_records(latest)[0]
                        last_completed = latest_timestamp
                        await asyncio.to_thread(get_platform().score_market, canonical, timeframe)
                        if with_forecast:
                            if forecast_task is None:
                                forecast_task = await begin_forecast(); payload["forecast_status"] = "refreshing"
                            else:
                                refresh_queued = True; payload["forecast_status"] = "queued"
                    next_bar_check = now + settings.bar_poll_seconds
                await websocket.send_json(payload)
                if forecast_task is not None and forecast_task.done():
                    try:
                        result = forecast_task.result()
                        result.update({"type": "projection_update", "accuracy": store.accuracy(canonical, timeframe)})
                        await websocket.send_json(result)
                    except Exception as exc:
                        await websocket.send_json({"type": "forecast_error", "detail": str(exc)})
                    finally:
                        forecast_task = None
                    if refresh_queued:
                        refresh_queued = False; forecast_task = await begin_forecast()
                        await websocket.send_json({"type": "forecast_status", "status": "refreshing_queued"})
            except Exception as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
            await asyncio.sleep(max(0.05, quote_poll - (time.monotonic() - loop_started)))
    except WebSocketDisconnect:
        if forecast_task:
            forecast_task.cancel()
