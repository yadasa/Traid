from __future__ import annotations

import atexit

import pandas as pd

from ..config import Settings
from ..market import get_timeframe, normalize_symbol
from .base import CandleProvider, MarketDataError, MarketQuote


class MT5Provider(CandleProvider):
    """Read completed candles and live ticks from a local MetaTrader 5 terminal."""

    name = "mt5"

    def __init__(self, settings: Settings):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise MarketDataError(
                "MetaTrader5 is not installed. Install requirements-live.txt on the "
                "Windows machine that runs your MT5 terminal."
            ) from exc

        self.mt5 = mt5
        self.aliases = settings.symbol_aliases()

        kwargs: dict[str, object] = {}
        if settings.mt5_terminal_path:
            kwargs["path"] = settings.mt5_terminal_path
        if settings.mt5_login is not None:
            kwargs["login"] = settings.mt5_login
        if settings.mt5_password:
            kwargs["password"] = settings.mt5_password
        if settings.mt5_server:
            kwargs["server"] = settings.mt5_server

        if not self.mt5.initialize(**kwargs):
            code, message = self.mt5.last_error()
            raise MarketDataError(f"MT5 initialization failed ({code}): {message}")
        atexit.register(self.mt5.shutdown)

    def _broker_symbol(self, symbol: str) -> tuple[str, str]:
        canonical = normalize_symbol(symbol)
        broker_symbol = self.aliases[canonical]
        if not self.mt5.symbol_select(broker_symbol, True):
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 could not select '{broker_symbol}' for {canonical} ({code}): {message}. "
                "Set TRAID_<SYMBOL>_SYMBOL to the exact broker symbol, including suffixes."
            )
        return canonical, broker_symbol

    @staticmethod
    def _rates_to_frame(rates) -> pd.DataFrame:
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        if "real_volume" in frame and frame["real_volume"].fillna(0).sum() > 0:
            frame["volume"] = frame["real_volume"]
        else:
            frame["volume"] = frame.get("tick_volume", 0.0)
        typical_price = frame[["open", "high", "low", "close"]].mean(axis=1)
        frame["amount"] = frame["volume"].astype(float) * typical_price
        return frame

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        _, broker_symbol = self._broker_symbol(symbol)
        tf = get_timeframe(timeframe)
        mt5_timeframe = getattr(self.mt5, tf.mt5_constant)

        # Position 0 is the still-forming candle. Start at 1 so Kronos receives
        # completed bars only and predictions do not repaint as the bar changes.
        rates = self.mt5.copy_rates_from_pos(broker_symbol, mt5_timeframe, 1, limit)
        if rates is None or len(rates) == 0:
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 returned no candles for {broker_symbol} ({code}): {message}"
            )
        return self.normalize_frame(self._rates_to_frame(rates), limit)

    def get_current_candle(self, symbol: str, timeframe: str) -> pd.DataFrame:
        _, broker_symbol = self._broker_symbol(symbol)
        tf = get_timeframe(timeframe)
        mt5_timeframe = getattr(self.mt5, tf.mt5_constant)
        rates = self.mt5.copy_rates_from_pos(broker_symbol, mt5_timeframe, 0, 1)
        if rates is None or len(rates) == 0:
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 returned no active candle for {broker_symbol} ({code}): {message}"
            )
        return self.normalize_frame(
            self._rates_to_frame(rates),
            limit=1,
            minimum_rows=1,
        )

    def get_quote(self, symbol: str) -> MarketQuote:
        canonical, broker_symbol = self._broker_symbol(symbol)
        tick = self.mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 returned no tick for {broker_symbol} ({code}): {message}"
            )

        bid = float(tick.bid) if getattr(tick, "bid", 0) > 0 else None
        ask = float(tick.ask) if getattr(tick, "ask", 0) > 0 else None
        last = float(tick.last) if getattr(tick, "last", 0) > 0 else None
        if last is not None:
            price = last
        elif bid is not None and ask is not None:
            price = (bid + ask) / 2
        else:
            price = bid or ask
        if price is None:
            raise MarketDataError(f"MT5 tick for {broker_symbol} has no usable price.")

        time_msc = getattr(tick, "time_msc", 0)
        if time_msc:
            timestamp = pd.to_datetime(time_msc, unit="ms", utc=True)
        else:
            timestamp = pd.to_datetime(tick.time, unit="s", utc=True)

        return MarketQuote(
            symbol=canonical,
            timestamp=timestamp,
            price=float(price),
            bid=bid,
            ask=ask,
            spread=(ask - bid) if bid is not None and ask is not None else None,
            delayed=False,
            source=f"mt5:{broker_symbol}",
        )
