from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()

SUPPORTED_SYMBOLS = ("XAUUSD", "XAGUSD", "NAS100", "SPX500")


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
    stream_poll_seconds: float = float(os.getenv("TRAID_STREAM_POLL_SECONDS", "5"))

    massive_api_key: str | None = os.getenv("MASSIVE_API_KEY")
    massive_base_url: str = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")

    mt5_terminal_path: str | None = os.getenv("MT5_TERMINAL_PATH")
    mt5_login: int | None = (
        int(os.environ["MT5_LOGIN"]) if os.getenv("MT5_LOGIN") else None
    )
    mt5_password: str | None = os.getenv("MT5_PASSWORD")
    mt5_server: str | None = os.getenv("MT5_SERVER")

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
