from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import Settings
from .market import normalize_symbol
from .providers.mt5 import MT5Provider


class TradingError(RuntimeError):
    """Raised when a trade request is unsafe, invalid, or rejected."""


@dataclass(frozen=True)
class MarketOrder:
    symbol: str
    side: Literal["buy", "sell"]
    volume: float
    stop_loss_distance: float
    take_profit_distance: float | None = None
    trailing_distance: float | None = None
    trailing_step: float = 0.0
    trailing_activation: float = 0.0
    deviation_points: int = 20
    client_order_id: str | None = None
    confirm_live: bool = False


@dataclass
class TrailingStopSpec:
    position_ticket: int
    symbol: str
    side: Literal["buy", "sell"]
    distance: float
    step: float
    activation: float


def next_trailing_stop(
    *,
    side: Literal["buy", "sell"],
    current_price: float,
    open_price: float,
    current_sl: float,
    distance: float,
    step: float,
    activation: float,
    min_stop_distance: float,
    digits: int,
) -> float | None:
    """Return a tighter stop level, or None when no trailing update is due."""
    if distance <= 0:
        return None
    step = max(0.0, step)
    activation = max(0.0, activation)
    min_stop_distance = max(0.0, min_stop_distance)

    if side == "buy":
        if current_price - open_price < activation:
            return None
        candidate = current_price - max(distance, min_stop_distance)
        if candidate >= current_price:
            return None
        if current_sl > 0 and candidate <= current_sl + step:
            return None
    else:
        if open_price - current_price < activation:
            return None
        candidate = current_price + max(distance, min_stop_distance)
        if candidate <= current_price:
            return None
        if current_sl > 0 and candidate >= current_sl - step:
            return None

    return round(candidate, digits)


class TrailingStopStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._specs: dict[int, TrailingStopSpec] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for item in payload:
                spec = TrailingStopSpec(**item)
                self._specs[spec.position_ticket] = spec
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt state file must never prevent protective server-side SLs
            # or market data from loading. Start with an empty trailing registry.
            self._specs = {}

    def _save(self) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = [asdict(spec) for spec in self._specs.values()]
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)

    def list(self) -> list[TrailingStopSpec]:
        with self._lock:
            return list(self._specs.values())

    def put(self, spec: TrailingStopSpec) -> None:
        with self._lock:
            self._specs[spec.position_ticket] = spec
            self._save()

    def remove(self, position_ticket: int) -> bool:
        with self._lock:
            removed = self._specs.pop(position_ticket, None) is not None
            if removed:
                self._save()
            return removed


class MT5TradeExecutor:
    def __init__(self, settings: Settings, provider: MT5Provider):
        self.settings = settings
        self.provider = provider
        self.mt5 = provider.mt5
        self.trailing = TrailingStopStore(settings.trailing_state_path)
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _assert_enabled(
        self,
        confirm_live: bool = False,
        require_live_confirmation: bool = True,
    ) -> None:
        if not self.settings.trading_enabled:
            raise TradingError(
                "Trading is disabled. Set TRAID_TRADING_ENABLED=true after testing in paper mode."
            )
        terminal = self.mt5.terminal_info()
        if terminal is None:
            raise TradingError(f"MT5 terminal information is unavailable: {self.mt5.last_error()}")
        if not getattr(terminal, "trade_allowed", False):
            raise TradingError("MT5 automated trading is disabled in the terminal.")
        if (
            require_live_confirmation
            and self.settings.trading_mode == "live"
            and not confirm_live
        ):
            raise TradingError("Live orders require confirm_live=true.")

    def status(self) -> dict[str, Any]:
        terminal = self.mt5.terminal_info()
        account = self.mt5.account_info()
        return {
            "enabled": self.settings.trading_enabled,
            "mode": self.settings.trading_mode,
            "terminal_connected": terminal is not None,
            "trade_allowed": bool(terminal and getattr(terminal, "trade_allowed", False)),
            "account": {
                "login": getattr(account, "login", None),
                "server": getattr(account, "server", None),
                "currency": getattr(account, "currency", None),
                "balance": getattr(account, "balance", None),
                "equity": getattr(account, "equity", None),
                "margin_free": getattr(account, "margin_free", None),
                "trade_mode": getattr(account, "trade_mode", None),
            }
            if account
            else None,
            "limits": {
                "max_order_lots": self.settings.max_order_lots,
                "max_open_positions": self.settings.max_open_positions,
                "max_positions_per_symbol": self.settings.max_positions_per_symbol,
                "require_stop_loss": self.settings.require_stop_loss,
            },
            "trailing_stops": [asdict(spec) for spec in self.trailing.list()],
        }

    def positions(self) -> list[dict[str, Any]]:
        positions = self.mt5.positions_get()
        if positions is None:
            raise TradingError(f"Could not read positions: {self.mt5.last_error()}")
        reverse_aliases = {value: key for key, value in self.provider.aliases.items()}
        trailing_by_ticket = {
            spec.position_ticket: asdict(spec) for spec in self.trailing.list()
        }
        output: list[dict[str, Any]] = []
        for position in positions:
            canonical = reverse_aliases.get(position.symbol)
            if canonical is None or int(position.magic) != self.settings.trading_magic:
                continue
            side = "buy" if position.type == self.mt5.POSITION_TYPE_BUY else "sell"
            output.append(
                {
                    "ticket": int(position.ticket),
                    "symbol": canonical,
                    "broker_symbol": position.symbol,
                    "side": side,
                    "volume": float(position.volume),
                    "open_price": float(position.price_open),
                    "current_price": float(position.price_current),
                    "stop_loss": float(position.sl),
                    "take_profit": float(position.tp),
                    "profit": float(position.profit),
                    "swap": float(position.swap),
                    "magic": int(position.magic),
                    "comment": position.comment,
                    "opened_at": int(position.time),
                    "trailing": trailing_by_ticket.get(int(position.ticket)),
                }
            )
        return output

    @staticmethod
    def _normalize_volume(volume: float, info, max_order_lots: float) -> float:
        if not math.isfinite(volume) or volume <= 0:
            raise TradingError("Volume must be a positive number of lots.")
        maximum = min(float(info.volume_max), max_order_lots)
        minimum = float(info.volume_min)
        step = float(info.volume_step)
        if volume < minimum or volume > maximum:
            raise TradingError(f"Volume must be between {minimum} and {maximum} lots.")
        steps = round((volume - minimum) / step)
        normalized = minimum + steps * step
        normalized = round(normalized, 8)
        if abs(normalized - volume) > max(step / 100, 1e-8):
            raise TradingError(f"Volume must follow the broker step of {step} lots.")
        return normalized

    def _position_for_result(self, broker_symbol: str, side: str) -> Any | None:
        positions = self.mt5.positions_get(symbol=broker_symbol) or ()
        expected_type = (
            self.mt5.POSITION_TYPE_BUY if side == "buy" else self.mt5.POSITION_TYPE_SELL
        )
        matching = [
            position
            for position in positions
            if position.type == expected_type and position.magic == self.settings.trading_magic
        ]
        return max(matching, key=lambda position: position.time_msc, default=None)

    def _preflight(self, request: dict[str, Any]) -> Any:
        """Run order_check and select a broker-supported filling policy."""
        original_filling = request.get("type_filling")
        if request.get("action") == self.mt5.TRADE_ACTION_DEAL:
            candidates = [
                original_filling,
                self.mt5.ORDER_FILLING_RETURN,
                self.mt5.ORDER_FILLING_IOC,
                self.mt5.ORDER_FILLING_FOK,
            ]
        else:
            candidates = [original_filling]

        seen: set[int | None] = set()
        errors: list[str] = []
        for filling in candidates:
            if filling in seen:
                continue
            seen.add(filling)
            candidate = dict(request)
            if filling is None:
                candidate.pop("type_filling", None)
            else:
                candidate["type_filling"] = filling
            check = self.mt5.order_check(candidate)
            if check is None:
                errors.append(str(self.mt5.last_error()))
                continue
            if int(check.retcode) == 0:
                request.clear()
                request.update(candidate)
                return check
            errors.append(f"{check.retcode}: {check.comment}")

        raise TradingError(
            "Order rejected during preflight. " + " | ".join(errors[-3:])
        )

    def place_market_order(self, order: MarketOrder) -> dict[str, Any]:
        with self._lock:
            self._assert_enabled(order.confirm_live)
            canonical, broker_symbol = self.provider._broker_symbol(order.symbol)
            if order.client_order_id and order.client_order_id in self._idempotency:
                return self._idempotency[order.client_order_id]

            positions = self.positions()
            if len(positions) >= self.settings.max_open_positions:
                raise TradingError("Maximum open-position limit reached.")
            symbol_positions = [p for p in positions if p["symbol"] == canonical]
            if len(symbol_positions) >= self.settings.max_positions_per_symbol:
                raise TradingError(f"Maximum open positions reached for {canonical}.")

            info = self.mt5.symbol_info(broker_symbol)
            tick = self.mt5.symbol_info_tick(broker_symbol)
            if info is None or tick is None:
                raise TradingError(f"Could not read live symbol information for {broker_symbol}.")
            volume = self._normalize_volume(
                order.volume,
                info,
                self.settings.max_order_lots,
            )
            if self.settings.require_stop_loss and order.stop_loss_distance <= 0:
                raise TradingError("A positive stop-loss distance is required.")

            is_buy = order.side == "buy"
            price = float(tick.ask if is_buy else tick.bid)
            digits = int(info.digits)
            point = float(info.point)
            minimum_stop = float(info.trade_stops_level) * point
            sl_distance = max(float(order.stop_loss_distance), minimum_stop)
            sl = round(price - sl_distance if is_buy else price + sl_distance, digits)
            tp = 0.0
            if order.take_profit_distance and order.take_profit_distance > 0:
                tp_distance = max(float(order.take_profit_distance), minimum_stop)
                tp = round(price + tp_distance if is_buy else price - tp_distance, digits)

            request = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": volume,
                "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": int(order.deviation_points),
                "magic": self.settings.trading_magic,
                "comment": "Traid market order",
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }
            check = self._preflight(request)
            base_result = {
                "mode": self.settings.trading_mode,
                "symbol": canonical,
                "broker_symbol": broker_symbol,
                "side": order.side,
                "volume": volume,
                "requested_price": price,
                "stop_loss": sl,
                "take_profit": tp or None,
                "preflight": check._asdict(),
            }

            if self.settings.trading_mode == "paper":
                result = {**base_result, "executed": False, "paper": True}
            else:
                send_result = self.mt5.order_send(request)
                if send_result is None:
                    raise TradingError(f"MT5 order_send failed: {self.mt5.last_error()}")
                accepted = {
                    self.mt5.TRADE_RETCODE_DONE,
                    self.mt5.TRADE_RETCODE_DONE_PARTIAL,
                    self.mt5.TRADE_RETCODE_PLACED,
                }
                if int(send_result.retcode) not in accepted:
                    raise TradingError(
                        f"Order failed ({send_result.retcode}): {send_result.comment}"
                    )
                position = self._position_for_result(broker_symbol, order.side)
                result = {
                    **base_result,
                    "executed": True,
                    "paper": False,
                    "result": send_result._asdict(),
                    "position_ticket": int(position.ticket) if position else None,
                    "fill_price": float(send_result.price),
                }
                if position and order.trailing_distance and order.trailing_distance > 0:
                    trailing_spec = TrailingStopSpec(
                        position_ticket=int(position.ticket),
                        symbol=canonical,
                        side=order.side,
                        distance=float(order.trailing_distance),
                        step=max(0.0, float(order.trailing_step)),
                        activation=max(0.0, float(order.trailing_activation)),
                    )
                    self.trailing.put(trailing_spec)
                    result["trailing"] = asdict(trailing_spec)

            if order.client_order_id:
                self._idempotency[order.client_order_id] = result
            return result

    def close_position(
        self,
        position_ticket: int,
        volume: float | None,
        confirm_live: bool,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_enabled(confirm_live)
            positions = self.mt5.positions_get(ticket=position_ticket)
            if not positions:
                raise TradingError(f"Position {position_ticket} was not found.")
            position = positions[0]
            reverse_aliases = {value: key for key, value in self.provider.aliases.items()}
            canonical = reverse_aliases.get(position.symbol)
            if canonical is None or int(position.magic) != self.settings.trading_magic:
                raise TradingError("This position is not managed by Traid.")
            info = self.mt5.symbol_info(position.symbol)
            tick = self.mt5.symbol_info_tick(position.symbol)
            if info is None or tick is None:
                raise TradingError(f"Could not read {position.symbol} market information.")
            close_volume = (
                float(position.volume)
                if volume is None
                else self._normalize_volume(volume, info, float(position.volume))
            )
            is_buy = position.type == self.mt5.POSITION_TYPE_BUY
            request = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "position": int(position.ticket),
                "symbol": position.symbol,
                "volume": close_volume,
                "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
                "price": float(tick.bid if is_buy else tick.ask),
                "deviation": 20,
                "magic": self.settings.trading_magic,
                "comment": "Traid close",
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_RETURN,
            }
            check = self._preflight(request)
            if self.settings.trading_mode == "paper":
                return {
                    "mode": "paper",
                    "executed": False,
                    "position_ticket": position_ticket,
                    "volume": close_volume,
                    "preflight": check._asdict(),
                }
            result = self.mt5.order_send(request)
            if result is None or int(result.retcode) not in {
                self.mt5.TRADE_RETCODE_DONE,
                self.mt5.TRADE_RETCODE_DONE_PARTIAL,
            }:
                detail = self.mt5.last_error() if result is None else result.comment
                code = None if result is None else result.retcode
                raise TradingError(f"Close failed ({code}): {detail}")
            remaining = self.mt5.positions_get(ticket=position_ticket)
            if not remaining:
                self.trailing.remove(position_ticket)
            return {
                "mode": "live",
                "executed": True,
                "position_ticket": position_ticket,
                "volume": close_volume,
                "result": result._asdict(),
            }

    def configure_trailing(self, spec: TrailingStopSpec) -> dict[str, Any]:
        self._assert_enabled(require_live_confirmation=False)
        positions = self.mt5.positions_get(ticket=spec.position_ticket)
        if not positions:
            raise TradingError(f"Position {spec.position_ticket} was not found.")
        position = positions[0]
        reverse_aliases = {value: key for key, value in self.provider.aliases.items()}
        canonical = reverse_aliases.get(position.symbol)
        if (
            canonical != normalize_symbol(spec.symbol)
            or int(position.magic) != self.settings.trading_magic
        ):
            raise TradingError("Position is not managed by Traid or its symbol does not match.")
        if spec.distance <= 0:
            raise TradingError("Trailing distance must be positive.")
        spec.side = "buy" if position.type == self.mt5.POSITION_TYPE_BUY else "sell"
        spec.symbol = canonical
        self.trailing.put(spec)
        return asdict(spec)

    def disable_trailing(self, position_ticket: int) -> bool:
        return self.trailing.remove(position_ticket)

    def process_trailing_once(self) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for spec in self.trailing.list():
            positions = self.mt5.positions_get(ticket=spec.position_ticket)
            if not positions:
                self.trailing.remove(spec.position_ticket)
                continue
            position = positions[0]
            if int(position.magic) != self.settings.trading_magic:
                self.trailing.remove(spec.position_ticket)
                continue
            info = self.mt5.symbol_info(position.symbol)
            tick = self.mt5.symbol_info_tick(position.symbol)
            if info is None or tick is None:
                continue
            side = "buy" if position.type == self.mt5.POSITION_TYPE_BUY else "sell"
            current_price = float(tick.bid if side == "buy" else tick.ask)
            candidate = next_trailing_stop(
                side=side,
                current_price=current_price,
                open_price=float(position.price_open),
                current_sl=float(position.sl),
                distance=spec.distance,
                step=spec.step,
                activation=spec.activation,
                min_stop_distance=float(info.trade_stops_level) * float(info.point),
                digits=int(info.digits),
            )
            if candidate is None:
                continue
            request = {
                "action": self.mt5.TRADE_ACTION_SLTP,
                "position": int(position.ticket),
                "symbol": position.symbol,
                "sl": candidate,
                "tp": float(position.tp),
                "magic": self.settings.trading_magic,
                "comment": "Traid trailing stop",
            }
            result = self.mt5.order_send(request)
            if result is not None and int(result.retcode) == self.mt5.TRADE_RETCODE_DONE:
                updates.append(
                    {
                        "position_ticket": int(position.ticket),
                        "old_stop_loss": float(position.sl),
                        "new_stop_loss": candidate,
                        "price": current_price,
                    }
                )
        return updates
