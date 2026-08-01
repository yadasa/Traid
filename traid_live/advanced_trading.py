from __future__ import annotations

import math
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .market import normalize_symbol
from .platform import PlatformStore
from .trading import MT5TradeExecutor, TradingError, TrailingStopSpec


PendingKind = Literal["buy_limit", "sell_limit", "buy_stop", "sell_stop"]
TrailingKind = Literal["fixed", "percent", "atr", "candle"]


@dataclass(frozen=True)
class PendingOrder:
    symbol: str
    kind: PendingKind
    volume: float
    price: float
    stop_loss: float
    take_profit: float | None = None
    expiration: int | None = None
    deviation_points: int = 20
    client_order_id: str | None = None
    confirm_live: bool = False
    comment: str = "Traid pending"


@dataclass(frozen=True)
class SmartTrailing:
    position_ticket: int
    kind: TrailingKind
    value: float
    activation: float = 0.0
    step: float = 0.0
    timeframe: str = "5m"
    lookback: int = 14


class AdvancedMT5Trader:
    def __init__(self, trader: MT5TradeExecutor, store: PlatformStore) -> None:
        self.trader = trader
        self.store = store
        self.mt5 = trader.mt5
        self.provider = trader.provider
        self.settings = trader.settings
        self._lock = threading.RLock()

    def _assert_enabled(self, confirm_live: bool = False) -> None:
        self.trader._assert_enabled(confirm_live)

    def pending_orders(self) -> list[dict[str, Any]]:
        orders = self.mt5.orders_get()
        if orders is None:
            raise TradingError(f"Could not read MT5 orders: {self.mt5.last_error()}")
        reverse = {value: key for key, value in self.provider.aliases.items()}
        output = []
        for order in orders:
            canonical = reverse.get(order.symbol)
            if canonical is None or int(order.magic) != self.settings.trading_magic:
                continue
            output.append({
                "ticket": int(order.ticket), "symbol": canonical, "broker_symbol": order.symbol,
                "type": int(order.type), "volume_initial": float(order.volume_initial),
                "volume_current": float(order.volume_current), "price_open": float(order.price_open),
                "stop_loss": float(order.sl), "take_profit": float(order.tp),
                "time_setup": int(order.time_setup), "time_expiration": int(order.time_expiration),
                "comment": order.comment,
            })
        return output

    @staticmethod
    def _pending_type(mt5: Any, kind: PendingKind) -> int:
        return {
            "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
            "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
            "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
            "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
        }[kind]

    def place_pending(self, order: PendingOrder) -> dict[str, Any]:
        with self._lock:
            self._assert_enabled(order.confirm_live)
            if order.client_order_id:
                remembered = self.store.idempotent_response(order.client_order_id)
                if remembered:
                    return remembered
            canonical, broker_symbol = self.provider._broker_symbol(order.symbol)
            info = self.mt5.symbol_info(broker_symbol)
            tick = self.mt5.symbol_info_tick(broker_symbol)
            if info is None or tick is None:
                raise TradingError(f"Could not read {broker_symbol} symbol information.")
            volume = self.trader._normalize_volume(order.volume, info, self.settings.max_order_lots)
            digits = int(info.digits)
            point = float(info.point)
            minimum = float(info.trade_stops_level) * point
            price = round(float(order.price), digits)
            sl = round(float(order.stop_loss), digits)
            tp = round(float(order.take_profit), digits) if order.take_profit else 0.0
            is_buy = order.kind.startswith("buy")
            if order.kind == "buy_limit" and price >= float(tick.ask):
                raise TradingError("A buy-limit price must be below the current Ask.")
            if order.kind == "sell_limit" and price <= float(tick.bid):
                raise TradingError("A sell-limit price must be above the current Bid.")
            if order.kind == "buy_stop" and price <= float(tick.ask):
                raise TradingError("A buy-stop price must be above the current Ask.")
            if order.kind == "sell_stop" and price >= float(tick.bid):
                raise TradingError("A sell-stop price must be below the current Bid.")
            if is_buy and sl >= price:
                raise TradingError("A buy Stop Loss must be below the entry price.")
            if not is_buy and sl <= price:
                raise TradingError("A sell Stop Loss must be above the entry price.")
            if abs(price - sl) < minimum:
                raise TradingError(f"Stop Loss must be at least {minimum} price units from entry.")
            request: dict[str, Any] = {
                "action": self.mt5.TRADE_ACTION_PENDING, "symbol": broker_symbol,
                "volume": volume, "type": self._pending_type(self.mt5, order.kind),
                "price": price, "sl": sl, "tp": tp, "deviation": order.deviation_points,
                "magic": self.settings.trading_magic, "comment": order.comment,
                "type_time": self.mt5.ORDER_TIME_SPECIFIED if order.expiration else self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }
            if order.expiration:
                request["expiration"] = int(order.expiration)
            check = self.trader._preflight(request)
            base = {"mode": self.settings.trading_mode, "symbol": canonical, "kind": order.kind, "volume": volume, "price": price, "stop_loss": sl, "take_profit": tp or None, "preflight": check._asdict()}
            if self.settings.trading_mode == "paper":
                result = {**base, "paper": True, "executed": False}
            else:
                sent = self.mt5.order_send(request)
                if sent is None or int(sent.retcode) not in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED}:
                    detail = self.mt5.last_error() if sent is None else sent.comment
                    code = None if sent is None else sent.retcode
                    raise TradingError(f"Pending order failed ({code}): {detail}")
                result = {**base, "paper": False, "executed": True, "order_ticket": int(sent.order), "result": sent._asdict()}
            if order.client_order_id:
                self.store.remember_order(order.client_order_id, result)
            self.store.audit("order.pending", entity_type="order", entity_id=result.get("order_ticket"), payload=result)
            return result

    def cancel_pending(self, ticket: int, confirm_live: bool = False) -> dict[str, Any]:
        with self._lock:
            self._assert_enabled(confirm_live)
            matching = self.mt5.orders_get(ticket=ticket)
            if not matching:
                raise TradingError(f"Pending order {ticket} was not found.")
            order = matching[0]
            if int(order.magic) != self.settings.trading_magic:
                raise TradingError("This pending order is not managed by Traid.")
            request = {"action": self.mt5.TRADE_ACTION_REMOVE, "order": int(ticket), "comment": "Traid cancel"}
            check = self.trader._preflight(request)
            if self.settings.trading_mode == "paper":
                return {"mode": "paper", "executed": False, "order_ticket": ticket, "preflight": check._asdict()}
            result = self.mt5.order_send(request)
            if result is None or int(result.retcode) != self.mt5.TRADE_RETCODE_DONE:
                detail = self.mt5.last_error() if result is None else result.comment
                raise TradingError(f"Cancel failed: {detail}")
            response = {"mode": "live", "executed": True, "order_ticket": ticket, "result": result._asdict()}
            self.store.audit("order.cancelled", entity_type="order", entity_id=ticket, payload=response)
            return response

    def modify_position(
        self, ticket: int, *, stop_loss: float | None = None,
        take_profit: float | None = None, confirm_live: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_enabled(confirm_live)
            positions = self.mt5.positions_get(ticket=ticket)
            if not positions:
                raise TradingError(f"Position {ticket} was not found.")
            position = positions[0]
            if int(position.magic) != self.settings.trading_magic:
                raise TradingError("This position is not managed by Traid.")
            info = self.mt5.symbol_info(position.symbol)
            tick = self.mt5.symbol_info_tick(position.symbol)
            if info is None or tick is None:
                raise TradingError("Could not read current symbol information.")
            digits = int(info.digits)
            minimum = float(info.trade_stops_level) * float(info.point)
            is_buy = position.type == self.mt5.POSITION_TYPE_BUY
            current = float(tick.bid if is_buy else tick.ask)
            sl = round(float(stop_loss), digits) if stop_loss is not None else float(position.sl)
            tp = round(float(take_profit), digits) if take_profit is not None else float(position.tp)
            if sl:
                if is_buy and (sl >= current or current - sl < minimum):
                    raise TradingError("Buy Stop Loss is invalid or too close to current Bid.")
                if not is_buy and (sl <= current or sl - current < minimum):
                    raise TradingError("Sell Stop Loss is invalid or too close to current Ask.")
            request = {"action": self.mt5.TRADE_ACTION_SLTP, "position": int(ticket), "symbol": position.symbol, "sl": sl, "tp": tp, "magic": self.settings.trading_magic, "comment": "Traid modify"}
            if self.settings.trading_mode == "paper":
                return {"mode": "paper", "executed": False, "position_ticket": ticket, "stop_loss": sl, "take_profit": tp}
            result = self.mt5.order_send(request)
            if result is None or int(result.retcode) != self.mt5.TRADE_RETCODE_DONE:
                detail = self.mt5.last_error() if result is None else result.comment
                raise TradingError(f"Position modification failed: {detail}")
            response = {"mode": "live", "executed": True, "position_ticket": ticket, "stop_loss": sl, "take_profit": tp, "result": result._asdict()}
            self.store.audit("position.modified", entity_type="position", entity_id=ticket, payload=response)
            return response

    def move_to_break_even(self, ticket: int, offset: float = 0.0, confirm_live: bool = False) -> dict[str, Any]:
        positions = self.mt5.positions_get(ticket=ticket)
        if not positions:
            raise TradingError(f"Position {ticket} was not found.")
        position = positions[0]
        is_buy = position.type == self.mt5.POSITION_TYPE_BUY
        target = float(position.price_open) + offset if is_buy else float(position.price_open) - offset
        return self.modify_position(ticket, stop_loss=target, confirm_live=confirm_live)

    def close_all(self, confirm_live: bool = False, symbol: str | None = None) -> list[dict[str, Any]]:
        canonical = normalize_symbol(symbol) if symbol else None
        results = []
        for position in self.trader.positions():
            if canonical and position["symbol"] != canonical:
                continue
            results.append(self.trader.close_position(position["ticket"], None, confirm_live))
        self.store.audit("positions.close_all", payload={"symbol": canonical, "count": len(results)})
        return results

    def create_oco(self, first: PendingOrder, second: PendingOrder) -> dict[str, Any]:
        if first.symbol != second.symbol:
            raise TradingError("OCO orders must use the same symbol.")
        group_id = str(uuid.uuid4())
        first_result = self.place_pending(first)
        second_result = self.place_pending(second)
        metadata = {"first": first_result, "second": second_result}
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO oco_groups(id,created_at,status,first_ticket,second_ticket,metadata_json) VALUES(?,?,?,?,?,?)",
                (group_id, self.store.get_setting("clock", None) or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "active", first_result.get("order_ticket"), second_result.get("order_ticket"), __import__("json").dumps(metadata, default=str)),
            )
        self.store.audit("oco.created", entity_type="oco", entity_id=group_id, payload=metadata)
        return {"id": group_id, "status": "active", **metadata}

    def reconcile_oco(self) -> list[dict[str, Any]]:
        if self.settings.trading_mode != "live":
            return []
        pending = {item["ticket"] for item in self.pending_orders()}
        actions = []
        with self.store.connection() as conn:
            groups = conn.execute("SELECT * FROM oco_groups WHERE status='active'").fetchall()
            for group in groups:
                first = group["first_ticket"]; second = group["second_ticket"]
                first_open = first in pending if first else False
                second_open = second in pending if second else False
                if first_open and not second_open:
                    actions.append(self.cancel_pending(first, True))
                elif second_open and not first_open:
                    actions.append(self.cancel_pending(second, True))
                else:
                    continue
                conn.execute("UPDATE oco_groups SET status='resolved' WHERE id=?", (group["id"],))
                self.store.audit("oco.resolved", entity_type="oco", entity_id=group["id"], payload={"actions": actions[-1:]})
        return actions

    def configure_smart_trailing(self, spec: SmartTrailing) -> dict[str, Any]:
        if spec.value <= 0:
            raise TradingError("Trailing value must be positive.")
        self.store.set_setting(f"smart_trailing:{spec.position_ticket}", asdict(spec))
        if spec.kind == "fixed":
            positions = self.trader.positions()
            position = next((item for item in positions if item["ticket"] == spec.position_ticket), None)
            if not position:
                raise TradingError(f"Position {spec.position_ticket} was not found.")
            self.trader.configure_trailing(
                TrailingStopSpec(
                    position_ticket=spec.position_ticket, symbol=position["symbol"], side=position["side"],
                    distance=spec.value, step=spec.step, activation=spec.activation,
                )
            )
        return asdict(spec)

    def process_smart_trailing_once(self) -> list[dict[str, Any]]:
        if self.settings.trading_mode != "live":
            return []
        updates = self.trader.process_trailing_once()
        settings = self.store.settings()
        positions = {item["ticket"]: item for item in self.trader.positions()}
        for key, payload in settings.items():
            if not key.startswith("smart_trailing:") or not isinstance(payload, dict):
                continue
            spec = SmartTrailing(**payload)
            if spec.kind == "fixed" or spec.position_ticket not in positions:
                continue
            position = positions[spec.position_ticket]
            info = self.mt5.symbol_info(position["broker_symbol"])
            tick = self.mt5.symbol_info_tick(position["broker_symbol"])
            if info is None or tick is None:
                continue
            is_buy = position["side"] == "buy"
            current = float(tick.bid if is_buy else tick.ask)
            if spec.activation and ((current - position["open_price"]) if is_buy else (position["open_price"] - current)) < spec.activation:
                continue
            if spec.kind == "percent":
                distance = current * spec.value / 100
            else:
                frame = self.provider.get_candles(position["symbol"], spec.timeframe, max(spec.lookback + 2, 20))
                if spec.kind == "atr":
                    previous_close = frame["close"].shift(1)
                    true_range = pd_concat_max(frame["high"] - frame["low"], (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs())
                    distance = float(true_range.tail(spec.lookback).mean()) * spec.value
                else:  # candle
                    recent = frame.tail(max(1, spec.lookback))
                    target = float(recent["low"].min()) if is_buy else float(recent["high"].max())
                    response = self.modify_position(spec.position_ticket, stop_loss=target, confirm_live=True)
                    updates.append(response)
                    continue
            target = current - distance if is_buy else current + distance
            old = float(position["stop_loss"] or 0)
            improves = target > old + spec.step if is_buy else old == 0 or target < old - spec.step
            if improves:
                response = self.modify_position(spec.position_ticket, stop_loss=target, confirm_live=True)
                updates.append(response)
        return updates


def pd_concat_max(*series):
    import pandas as pd

    return pd.concat(series, axis=1).max(axis=1)
