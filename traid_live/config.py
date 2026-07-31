from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()

SUPPORTED_SYMBOLS = ("XAUUSD", "XAGUSD", "NAS100", "SPX500")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables and an optional .env file."""

    provider: str = os.getenv("TRAID_PROVIDER", "mt5").lower()
    model_id: str = os.getenv("TRAID_MODEL_ID", "NeoQuasar/Kronos-small")
    tokenizer_id: str = os.getenv(
        "TRAID_TOKENIZER_ID", "NeoQuasar/Kronos-Tokenizer-base"
    )
    model_device: str | None = os.getenv("TRAID_DEVICE") or None
    max_context: int = int(os.getenv("TRAID_MAX_CONTEXT", "512"))
    default_timeframe: str = os.getenv("TRAID_TIMEFRAME", "5m")
    default_lookback: int = int(os.getenv("TRAID_LOOKBACK", "400"))
    default_pred_len: int = int(os.getenv("TRAID_PRED_LEN", "24"))
    quote_poll_seconds: float = float(
        os.getenv(
            "TRAID_QUOTE_POLL_SECONDS",
            os.getenv("TRAID_STREAM_POLL_SECONDS", "0.5"),
        )
    )
    bar_poll_seconds: float = float(os.getenv("TRAID_BAR_POLL_SECONDS", "2"))

    massive_api_key: str | None = os.getenv("MASSIVE_API_KEY")
    massive_base_url: str = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")

    mt5_terminal_path: str | None = os.getenv("MT5_TERMINAL_PATH")
    mt5_login: int | None = (
        int(os.environ["MT5_LOGIN"]) if os.getenv("MT5_LOGIN") else None
    )
    mt5_password: str | None = os.getenv("MT5_PASSWORD")
    mt5_server: str | None = os.getenv("MT5_SERVER")

    trading_enabled: bool = env_bool("TRAID_TRADING_ENABLED", False)
    trading_mode: str = os.getenv("TRAID_TRADING_MODE", "paper").lower()
    trading_api_key: str | None = os.getenv("TRAID_TRADING_API_KEY")
    trading_magic: int = int(os.getenv("TRAID_TRADING_MAGIC", "260731"))
    max_order_lots: float = float(os.getenv("TRAID_MAX_ORDER_LOTS", "1.0"))
    max_open_positions: int = int(os.getenv("TRAID_MAX_OPEN_POSITIONS", "4"))
    max_positions_per_symbol: int = int(
        os.getenv("TRAID_MAX_POSITIONS_PER_SYMBOL", "1")
    )
    require_stop_loss: bool = env_bool("TRAID_REQUIRE_STOP_LOSS", True)
    trailing_poll_seconds: float = float(
        os.getenv("TRAID_TRAILING_POLL_SECONDS", "0.5")
    )
    trailing_state_path: str = os.getenv(
        "TRAID_TRAILING_STATE_PATH", "data/trailing_stops.json"
    )

    cors_origins: tuple[str, ...] = tuple(
        value.strip()
        for value in os.getenv("TRAID_CORS_ORIGINS", "*").split(",")
        if value.strip()
    )

    def symbol_aliases(self) -> dict[str, str]:
        defaults = {
            "XAUUSD": "XAUUSD",
            "XAGUSD": "XAGUSD",
            "NAS100": "NAS100",
            "SPX500": "SPX500",
        }
        return {
            symbol: os.getenv(f"TRAID_{symbol}_SYMBOL", default)
            for symbol, default in defaults.items()
        }

    def validate(self) -> None:
        if self.provider not in {"mt5", "massive"}:
            raise ValueError("TRAID_PROVIDER must be either 'mt5' or 'massive'.")
        if self.provider == "massive" and not self.massive_api_key:
            raise ValueError("MASSIVE_API_KEY is required when TRAID_PROVIDER=massive.")
        if self.max_context < 2:
            raise ValueError("TRAID_MAX_CONTEXT must be at least 2.")
        if self.quote_poll_seconds < 0.1:
            raise ValueError("TRAID_QUOTE_POLL_SECONDS must be at least 0.1.")
        if self.bar_poll_seconds < 0.5:
            raise ValueError("TRAID_BAR_POLL_SECONDS must be at least 0.5.")
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRAID_TRADING_MODE must be 'paper' or 'live'.")
        if self.trading_enabled and self.provider != "mt5":
            raise ValueError("Live order execution currently requires TRAID_PROVIDER=mt5.")
        if self.trading_enabled and not self.trading_api_key:
            raise ValueError(
                "TRAID_TRADING_API_KEY is required whenever trading is enabled."
            )
        if self.max_order_lots <= 0:
            raise ValueError("TRAID_MAX_ORDER_LOTS must be positive.")
        if self.max_open_positions < 1 or self.max_positions_per_symbol < 1:
            raise ValueError("Trading position limits must be at least 1.")
        if self.trailing_poll_seconds < 0.1:
            raise ValueError("TRAID_TRAILING_POLL_SECONDS must be at least 0.1.")
