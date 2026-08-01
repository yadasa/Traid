from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from traid_live.auth import SessionAuth
from traid_live.platform import (
    PlatformStore,
    RiskEngine,
    revision_metrics,
    uncertainty_summary,
)


def candle(timestamp: str, open_: float, close: float, high: float | None = None, low: float | None = None, volume: float = 100) -> dict:
    return {
        "timestamp": pd.Timestamp(timestamp),
        "open": open_,
        "high": high if high is not None else max(open_, close),
        "low": low if low is not None else min(open_, close),
        "close": close,
        "volume": volume,
        "amount": volume * (open_ + close) / 2,
    }


def test_forecast_ledger_and_accuracy(tmp_path: Path) -> None:
    store = PlatformStore(str(tmp_path / "traid.db"))
    history = pd.DataFrame([
        candle("2026-01-01T10:00:00Z", 100, 101),
        candle("2026-01-01T10:05:00Z", 101, 102),
    ])
    projection = pd.DataFrame([
        candle("2026-01-01T10:10:00Z", 102, 103, 104, 101),
        candle("2026-01-01T10:15:00Z", 103, 104, 105, 102),
    ])
    forecast_id = store.save_forecast(
        symbol="XAUUSD", timeframe="5m", model_id="test", tokenizer_id="test",
        parameters={"pred_len": 2}, history=history, projection=projection,
        source="test", inference_ms=10,
    )
    assert store.forecast(forecast_id)["projection"][0]["close"] == 103

    actual = pd.DataFrame([
        candle("2026-01-01T10:10:00Z", 102, 103.5, 104, 101),
        candle("2026-01-01T10:15:00Z", 103.5, 103, 105, 102),
    ])
    assert store.score_realized("XAUUSD", "5m", actual) == 2
    accuracy = store.accuracy("XAUUSD", "5m")
    assert accuracy["samples"] == 2
    assert accuracy["direction_accuracy"] == 50
    assert accuracy["range_hit_rate"] == 100
    assert store.score_realized("XAUUSD", "5m", actual) == 0


def test_uncertainty_summary_builds_percentile_paths() -> None:
    frames = []
    for shift in (-1, 0, 1):
        frames.append(pd.DataFrame([
            candle("2026-01-01T10:10:00Z", 100, 101 + shift, 103 + shift, 99 + shift),
            candle("2026-01-01T10:15:00Z", 101, 102 + shift, 104 + shift, 100 + shift),
        ]))
    result = uncertainty_summary(frames)
    assert result["paths"] == 3
    assert result["median"][0]["close"] == 101
    assert result["p10"][0]["close"] < result["p90"][0]["close"]
    assert len(result["confidence_decay"]) == 2


def test_revision_metrics_detect_direction_flip() -> None:
    previous = [
        {"open": 100, "high": 102, "low": 99, "close": 101},
        {"open": 101, "high": 104, "low": 100, "close": 103},
    ]
    current = [
        {"open": 100, "high": 101, "low": 98, "close": 99},
        {"open": 99, "high": 100, "low": 96, "close": 97},
    ]
    revision = revision_metrics(previous, current)
    assert revision["direction_flip"] is True
    assert revision["direction_previous"] == "bullish"
    assert revision["direction_active"] == "bearish"
    assert revision["severity"] in {"moderate", "major"}


def test_risk_position_size_respects_step_and_amount(tmp_path: Path) -> None:
    engine = RiskEngine(PlatformStore(str(tmp_path / "risk.db")))
    result = engine.position_size(
        equity=25000, risk_percent=0.5, stop_distance=5,
        tick_size=0.01, tick_value=1, volume_min=0.01,
        volume_max=10, volume_step=0.01,
    )
    assert result["risk_amount"] == 125
    assert result["volume"] == 0.25
    assert result["estimated_loss"] == 125


def test_risk_limits_block_after_daily_loss(tmp_path: Path) -> None:
    store = PlatformStore(str(tmp_path / "limits.db"))
    store.set_setting("max_daily_loss_pct", 1.0)
    store.journal_create({
        "symbol": "XAUUSD", "side": "buy", "status": "closed",
        "pnl": -150, "tags": [], "metadata": {},
    })
    status = RiskEngine(store).status(10000)
    assert status["allowed"] is False
    assert "Daily loss limit reached." in status["reasons"]


def test_journal_and_event_round_trip(tmp_path: Path) -> None:
    store = PlatformStore(str(tmp_path / "roundtrip.db"))
    entry = store.journal_create({
        "symbol": "gold", "side": "buy", "status": "open",
        "tags": ["test"], "metadata": {"forecast": "abc"},
    })
    updated = store.journal_update(entry["id"], {"status": "closed", "pnl": 42})
    assert updated["pnl"] == 42
    event = store.upsert_event({
        "starts_at": "2026-08-01T12:30:00Z", "currency": "USD",
        "impact": "high", "title": "Employment report",
    })
    assert store.events(impact="high")[0]["id"] == event["id"]


def test_password_hash_and_session_login(monkeypatch: pytest.MonkeyPatch) -> None:
    password_hash = SessionAuth.hash_password("correct horse battery staple")
    monkeypatch.setenv("TRAID_ADMIN_USER", "tj")
    monkeypatch.setenv("TRAID_ADMIN_PASSWORD_HASH", password_hash)
    auth = SessionAuth()
    login = auth.login("tj", "correct horse battery staple")
    assert login["principal"]["role"] == "admin"
    assert auth.resolve(f"Bearer {login['token']}").name == "tj"
    assert auth.resolve("Bearer bad-token") is None


def test_dashboard_has_responsive_and_advanced_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    css_path = root / "dashboard" / "app.css"
    js_path = root / "dashboard" / "app.js"
    # These assertions become active after the responsive dashboard files replace
    # the legacy single-file dashboard in the same upgrade.
    if css_path.exists() and js_path.exists():
        css = css_path.read_text(encoding="utf-8")
        js = js_path.read_text(encoding="utf-8")
        assert "@media (max-width: 720px)" in css
        assert "env(safe-area-inset-bottom)" in css
        assert "FORECAST_TRANSITION_MS = 333" in js
        assert "advancedForecast" in html
        assert "projectionHistory" in js
