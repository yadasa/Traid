from __future__ import annotations

from dataclasses import dataclass

from .config import SUPPORTED_SYMBOLS


@dataclass(frozen=True)
class Timeframe:
    name: str
    pandas_freq: str
    seconds: int
    mt5_constant: str
    massive_multiplier: int
    massive_timespan: str


TIMEFRAMES: dict[str, Timeframe] = {
    "1m": Timeframe("1m", "1min", 60, "TIMEFRAME_M1", 1, "minute"),
    "5m": Timeframe("5m", "5min", 300, "TIMEFRAME_M5", 5, "minute"),
    "15m": Timeframe("15m", "15min", 900, "TIMEFRAME_M15", 15, "minute"),
    "30m": Timeframe("30m", "30min", 1800, "TIMEFRAME_M30", 30, "minute"),
    "1h": Timeframe("1h", "1h", 3600, "TIMEFRAME_H1", 1, "hour"),
    "4h": Timeframe("4h", "4h", 14400, "TIMEFRAME_H4", 4, "hour"),
    "1d": Timeframe("1d", "1D", 86400, "TIMEFRAME_D1", 1, "day"),
}

MASSIVE_SYMBOLS = {
    "XAUUSD": "C:XAUUSD",
    "XAGUSD": "C:XAGUSD",
    "NAS100": "I:NDX",
    "SPX500": "I:SPX",
}

INDEX_SYMBOLS = {"NAS100", "SPX500"}
METAL_SYMBOLS = {"XAUUSD", "XAGUSD"}


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace("/", "").replace("-", "").strip()
    aliases = {
        "XAU": "XAUUSD",
        "GOLD": "XAUUSD",
        "XAG": "XAGUSD",
        "SILVER": "XAGUSD",
        "NDX": "NAS100",
        "US100": "NAS100",
        "NAS100": "NAS100",
        "SPX": "SPX500",
        "US500": "SPX500",
        "SP500": "SPX500",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_SYMBOLS:
        raise ValueError(
            f"Unsupported symbol '{symbol}'. Supported symbols: {', '.join(SUPPORTED_SYMBOLS)}"
        )
    return normalized


def get_timeframe(name: str) -> Timeframe:
    normalized = name.lower().strip()
    try:
        return TIMEFRAMES[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported timeframe '{name}'. Supported: {', '.join(TIMEFRAMES)}"
        ) from exc
