from __future__ import annotations

from ..config import Settings
from .base import CandleProvider
from .massive import MassiveProvider
from .mt5 import MT5Provider


def build_provider(settings: Settings) -> CandleProvider:
    settings.validate()
    if settings.provider == "mt5":
        return MT5Provider(settings)
    if settings.provider == "massive":
        return MassiveProvider(settings)
    raise ValueError(f"Unsupported provider: {settings.provider}")
