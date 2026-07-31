from __future__ import annotations

import atexit

import pandas as pd

from ..config import Settings
from ..market import get_timeframe, normalize_symbol
from .base import CandleProvider, MarketDataError


class MT5Provider(CandleProvider):
    """Read completed candles from the locally running MetaTrader 5 terminal."""

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

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        canonical = normalize_symbol(symbol)
        tf = get_timeframe(timeframe)
        broker_symbol = self.aliases[canonical]

        if not self.mt5.symbol_select(broker_symbol, True):
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 could not select '{broker_symbol}' for {canonical} ({code}): {message}. "
                "Set TRAID_<SYMBOL>_SYMBOL to the exact broker symbol, including suffixes."
            )

        mt5_timeframe = getattr(self.mt5, tf.mt5_constant)
        # Position 0 is the still-forming candle. Start at 1 so Kronos receives
        # completed bars only and predictions do not repaint as the bar changes.
        rates = self.mt5.copy_rates_from_pos(broker_symbol, mt5_timeframe, 1, limit)
        if rates is None or len(rates) == 0:
            code, message = self.mt5.last_error()
            raise MarketDataError(
                f"MT5 returned no candles for {broker_symbol} ({code}): {message}"
            )

        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame["volume"] = frame.get("real_volume", frame.get("tick_volume", 0.0))
        typical_price = frame[["open", "high", "low", "close"]].mean(axis=1)
        frame["amount"] = frame["volume"].astype(float) * typical_price
        return self.normalize_frame(frame, limit)
