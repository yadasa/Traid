from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from .forecast import ForecastEngine, ForecastParameters
from .market import TIMEFRAMES, normalize_symbol


UTC = timezone.utc


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output = frame.copy()
    if "timestamp" in output.columns:
        output["timestamp"] = pd.to_datetime(output["timestamp"], utc=True).map(
            lambda value: value.isoformat()
        )
    return output.to_dict(orient="records")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS forecasts (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    input_last_timestamp TEXT NOT NULL,
    model_id TEXT NOT NULL,
    tokenizer_id TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    history_json TEXT NOT NULL,
    projection_json TEXT NOT NULL,
    uncertainty_json TEXT,
    revision_json TEXT,
    inference_ms REAL NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecasts_market
ON forecasts(symbol,timeframe,generated_at DESC);
CREATE TABLE IF NOT EXISTS forecast_scores (
    forecast_id TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    target_timestamp TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    actual_json TEXT NOT NULL,
    close_error REAL NOT NULL,
    close_error_pct REAL NOT NULL,
    direction_correct INTEGER NOT NULL,
    range_hit INTEGER NOT NULL,
    high_error REAL NOT NULL,
    low_error REAL NOT NULL,
    volume_error_pct REAL,
    PRIMARY KEY(forecast_id,horizon),
    FOREIGN KEY(forecast_id) REFERENCES forecasts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS journal (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position_ticket INTEGER,
    order_ticket INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    volume REAL,
    risk_amount REAL,
    forecast_id TEXT,
    entry_reason TEXT,
    exit_reason TEXT,
    notes TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    pnl REAL,
    mfe REAL,
    mae REAL
);
CREATE INDEX IF NOT EXISTS idx_journal_created ON journal(created_at DESC);
CREATE TABLE IF NOT EXISTS economic_events (
    id TEXT PRIMARY KEY,
    starts_at TEXT NOT NULL,
    currency TEXT NOT NULL,
    impact TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT,
    actual TEXT,
    forecast TEXT,
    previous TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_starts ON economic_events(starts_at);
CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_requests (
    client_order_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oco_groups (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    first_ticket INTEGER,
    second_ticket INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


DEFAULT_SETTINGS: dict[str, Any] = {
    "advanced_forecast": False,
    "uncertainty_paths": 7,
    "risk_per_trade_pct": 0.5,
    "max_daily_loss_pct": 2.0,
    "max_weekly_drawdown_pct": 5.0,
    "max_total_open_risk_pct": 3.0,
    "max_consecutive_losses": 4,
    "trading_disabled": False,
    "notifications_enabled": True,
    "event_blackout_minutes": 0,
}


class PlatformStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("TRAID_DATABASE_PATH", "data/traid.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as conn:
            conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            if self.get_setting(key, None) is None:
                self.set_setting(key, value)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def audit(
        self,
        action: str,
        *,
        actor: str = "system",
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log(created_at,actor,action,entity_type,entity_id,payload_json) VALUES(?,?,?,?,?,?)",
                (utc_now_iso(), actor, action, entity_type, str(entity_id) if entity_id is not None else None, _json(payload or {})),
            )

    def audit_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connection() as conn:
            row = conn.execute("SELECT value_json FROM platform_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def settings(self) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute("SELECT key,value_json FROM platform_settings").fetchall()
        values = dict(DEFAULT_SETTINGS)
        values.update({row["key"]: json.loads(row["value_json"]) for row in rows})
        return values

    def set_setting(self, key: str, value: Any, actor: str = "system") -> Any:
        with self._lock, self.connection() as conn:
            conn.execute(
                "INSERT INTO platform_settings(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, _json(value), utc_now_iso()),
            )
        self.audit("setting.updated", actor=actor, entity_type="setting", entity_id=key, payload={"value": value})
        return value

    def save_forecast(
        self,
        *,
        symbol: str,
        timeframe: str,
        model_id: str,
        tokenizer_id: str,
        parameters: dict[str, Any],
        history: pd.DataFrame,
        projection: pd.DataFrame,
        source: str,
        inference_ms: float,
        uncertainty: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
    ) -> str:
        forecast_id = str(uuid.uuid4())
        historical = _rows(history)
        projected = _rows(projection)
        if not historical:
            raise ValueError("Forecast history cannot be empty.")
        with self._lock, self.connection() as conn:
            conn.execute(
                """INSERT INTO forecasts(
                    id,symbol,timeframe,generated_at,input_last_timestamp,model_id,tokenizer_id,
                    parameters_json,history_json,projection_json,uncertainty_json,revision_json,
                    inference_ms,source
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    forecast_id,
                    normalize_symbol(symbol),
                    timeframe,
                    utc_now_iso(),
                    historical[-1]["timestamp"],
                    model_id,
                    tokenizer_id,
                    _json(parameters),
                    _json(historical),
                    _json(projected),
                    _json(uncertainty) if uncertainty else None,
                    _json(revision) if revision else None,
                    inference_ms,
                    source,
                ),
            )
        self.audit("forecast.created", entity_type="forecast", entity_id=forecast_id, payload={"symbol": symbol, "timeframe": timeframe})
        return forecast_id

    @staticmethod
    def _forecast_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("parameters_json", "parameters"),
            ("history_json", "history"),
            ("projection_json", "projection"),
            ("uncertainty_json", "uncertainty"),
            ("revision_json", "revision"),
        ):
            item[target] = json.loads(item.pop(source)) if item[source] else None
        return item

    def forecast(self, forecast_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM forecasts WHERE id=?", (forecast_id,)).fetchone()
        return self._forecast_row(row) if row else None

    def forecasts(self, symbol: str, timeframe: str, limit: int = 25) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM forecasts WHERE symbol=? AND timeframe=? ORDER BY generated_at DESC LIMIT ?",
                (normalize_symbol(symbol), timeframe, max(1, min(limit, 500))),
            ).fetchall()
        return [self._forecast_row(row) for row in rows]

    def accuracy(self, symbol: str, timeframe: str, limit: int = 1000) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT s.* FROM forecast_scores s JOIN forecasts f ON f.id=s.forecast_id
                   WHERE f.symbol=? AND f.timeframe=? ORDER BY s.scored_at DESC LIMIT ?""",
                (normalize_symbol(symbol), timeframe, max(1, min(limit, 10000))),
            ).fetchall()
        if not rows:
            return {"samples": 0, "direction_accuracy": None, "mean_close_error_pct": None, "range_hit_rate": None, "by_horizon": {}}
        data = [dict(row) for row in rows]
        by_horizon: dict[str, dict[str, Any]] = {}
        for horizon in sorted({int(item["horizon"]) for item in data}):
            subset = [item for item in data if int(item["horizon"]) == horizon]
            by_horizon[str(horizon)] = _score_summary(subset)
        return {**_score_summary(data), "by_horizon": by_horizon}

    def score_realized(self, symbol: str, timeframe: str, actual: pd.DataFrame) -> int:
        canonical = normalize_symbol(symbol)
        actual_rows = {
            pd.Timestamp(row["timestamp"]).isoformat(): row
            for row in actual.to_dict(orient="records")
        }
        if not actual_rows:
            return 0
        with self.connection() as conn:
            forecasts = conn.execute(
                "SELECT id,history_json,projection_json FROM forecasts WHERE symbol=? AND timeframe=?",
                (canonical, timeframe),
            ).fetchall()
            existing = {
                (row["forecast_id"], int(row["horizon"]))
                for row in conn.execute("SELECT forecast_id,horizon FROM forecast_scores").fetchall()
            }
        inserted = 0
        with self._lock, self.connection() as conn:
            for forecast in forecasts:
                history = json.loads(forecast["history_json"])
                projection = json.loads(forecast["projection_json"])
                base_close = float(history[-1]["close"])
                for index, predicted in enumerate(projection, start=1):
                    key = (forecast["id"], index)
                    timestamp = pd.Timestamp(predicted["timestamp"]).isoformat()
                    observed = actual_rows.get(timestamp)
                    if key in existing or observed is None:
                        continue
                    predicted_close = float(predicted["close"])
                    actual_close = float(observed["close"])
                    predicted_direction = math.copysign(1, predicted_close - base_close) if predicted_close != base_close else 0
                    actual_direction = math.copysign(1, actual_close - base_close) if actual_close != base_close else 0
                    volume_actual = float(observed.get("volume") or 0)
                    volume_predicted = float(predicted.get("volume") or 0)
                    volume_error = abs(volume_predicted - volume_actual) / volume_actual * 100 if volume_actual else None
                    conn.execute(
                        """INSERT INTO forecast_scores(
                            forecast_id,horizon,target_timestamp,scored_at,actual_json,close_error,
                            close_error_pct,direction_correct,range_hit,high_error,low_error,volume_error_pct
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            forecast["id"], index, timestamp, utc_now_iso(), _json(observed),
                            abs(predicted_close - actual_close),
                            abs(predicted_close - actual_close) / max(abs(actual_close), 1e-12) * 100,
                            int(predicted_direction == actual_direction),
                            int(float(predicted["low"]) <= actual_close <= float(predicted["high"])),
                            abs(float(predicted["high"]) - float(observed["high"])),
                            abs(float(predicted["low"]) - float(observed["low"])),
                            volume_error,
                        ),
                    )
                    inserted += 1
        return inserted

    def idempotent_response(self, client_order_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT response_json FROM order_requests WHERE client_order_id=?", (client_order_id,)).fetchone()
        return json.loads(row["response_json"]) if row else None

    def remember_order(self, client_order_id: str, response: dict[str, Any]) -> None:
        with self._lock, self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO order_requests(client_order_id,created_at,response_json) VALUES(?,?,?)",
                (client_order_id, utc_now_iso(), _json(response)),
            )

    def journal_create(self, payload: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        journal_id = payload.get("id") or str(uuid.uuid4())
        now = utc_now_iso()
        fields = {
            "id": journal_id, "created_at": now, "updated_at": now,
            "position_ticket": payload.get("position_ticket"), "order_ticket": payload.get("order_ticket"),
            "symbol": normalize_symbol(payload["symbol"]), "side": payload["side"],
            "status": payload.get("status", "open"), "entry_price": payload.get("entry_price"),
            "exit_price": payload.get("exit_price"), "volume": payload.get("volume"),
            "risk_amount": payload.get("risk_amount"), "forecast_id": payload.get("forecast_id"),
            "entry_reason": payload.get("entry_reason"), "exit_reason": payload.get("exit_reason"),
            "notes": payload.get("notes"), "tags_json": _json(payload.get("tags", [])),
            "metadata_json": _json(payload.get("metadata", {})), "pnl": payload.get("pnl"),
            "mfe": payload.get("mfe"), "mae": payload.get("mae"),
        }
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in fields)
        with self._lock, self.connection() as conn:
            conn.execute(f"INSERT INTO journal({columns}) VALUES({placeholders})", tuple(fields.values()))
        self.audit("journal.created", actor=actor, entity_type="journal", entity_id=journal_id, payload=payload)
        return self.journal_entry(journal_id) or fields

    def journal_entry(self, journal_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM journal WHERE id=?", (journal_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def journal_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM journal ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            output.append(item)
        return output

    def journal_update(self, journal_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        allowed = {"position_ticket", "order_ticket", "status", "entry_price", "exit_price", "volume", "risk_amount", "forecast_id", "entry_reason", "exit_reason", "notes", "pnl", "mfe", "mae"}
        updates = {key: value for key, value in patch.items() if key in allowed}
        if "tags" in patch:
            updates["tags_json"] = _json(patch["tags"])
        if "metadata" in patch:
            updates["metadata_json"] = _json(patch["metadata"])
        updates["updated_at"] = utc_now_iso()
        if not updates:
            raise ValueError("No supported journal fields were provided.")
        clause = ",".join(f"{key}=?" for key in updates)
        with self._lock, self.connection() as conn:
            cursor = conn.execute(f"UPDATE journal SET {clause} WHERE id=?", (*updates.values(), journal_id))
            if cursor.rowcount == 0:
                raise KeyError(journal_id)
        self.audit("journal.updated", actor=actor, entity_type="journal", entity_id=journal_id, payload=patch)
        return self.journal_entry(journal_id) or {}

    def upsert_event(self, event: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        event_id = str(event.get("id") or uuid.uuid4())
        starts_at = pd.Timestamp(event["starts_at"])
        starts_at = starts_at.tz_localize("UTC") if starts_at.tzinfo is None else starts_at.tz_convert("UTC")
        with self._lock, self.connection() as conn:
            conn.execute(
                """INSERT INTO economic_events(id,starts_at,currency,impact,title,source,actual,forecast,previous,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   starts_at=excluded.starts_at,currency=excluded.currency,impact=excluded.impact,
                   title=excluded.title,source=excluded.source,actual=excluded.actual,
                   forecast=excluded.forecast,previous=excluded.previous,metadata_json=excluded.metadata_json""",
                (event_id, starts_at.isoformat(), event.get("currency", "USD").upper(), event.get("impact", "medium").lower(), event["title"], event.get("source"), event.get("actual"), event.get("forecast"), event.get("previous"), _json(event.get("metadata", {}))),
            )
        self.audit("event.upserted", actor=actor, entity_type="event", entity_id=event_id, payload=event)
        return {**event, "id": event_id, "starts_at": starts_at.isoformat()}

    def events(self, start: str | None = None, end: str | None = None, impact: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if start:
            clauses.append("starts_at>=?"); values.append(pd.Timestamp(start).isoformat())
        if end:
            clauses.append("starts_at<=?"); values.append(pd.Timestamp(end).isoformat())
        if impact:
            clauses.append("impact=?"); values.append(impact.lower())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM economic_events{where} ORDER BY starts_at", values).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata_json"])} for row in rows]


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_weekly_drawdown_pct: float = 5.0
    max_total_open_risk_pct: float = 3.0
    max_consecutive_losses: int = 4


class RiskEngine:
    def __init__(self, store: PlatformStore) -> None:
        self.store = store

    def limits(self) -> RiskLimits:
        settings = self.store.settings()
        return RiskLimits(**{key: settings[key] for key in asdict(RiskLimits()).keys()})

    @staticmethod
    def position_size(
        *, equity: float, risk_percent: float, stop_distance: float,
        tick_size: float, tick_value: float, volume_min: float,
        volume_max: float, volume_step: float,
    ) -> dict[str, float]:
        values = (equity, risk_percent, stop_distance, tick_size, tick_value, volume_min, volume_max, volume_step)
        if not all(math.isfinite(float(value)) and float(value) > 0 for value in values):
            raise ValueError("All risk-sizing values must be positive finite numbers.")
        risk_amount = equity * risk_percent / 100
        loss_per_lot = stop_distance / tick_size * tick_value
        raw = risk_amount / loss_per_lot
        stepped = math.floor((raw - volume_min + 1e-12) / volume_step) * volume_step + volume_min
        volume = min(volume_max, max(volume_min, stepped))
        precision = max(0, int(round(-math.log10(volume_step)))) if volume_step < 1 else 0
        volume = round(volume, precision + 2)
        estimated_loss = loss_per_lot * volume
        return {"volume": volume, "risk_amount": risk_amount, "estimated_loss": estimated_loss, "raw_volume": raw, "loss_per_lot": loss_per_lot}

    def status(self, equity: float, open_risk: float = 0.0) -> dict[str, Any]:
        entries = self.store.journal_entries(2000)
        now = pd.Timestamp.now(tz="UTC")
        day_start = now.normalize()
        week_start = day_start - pd.Timedelta(days=day_start.weekday())
        closed = [item for item in entries if item.get("status") == "closed" and item.get("pnl") is not None]
        daily_pnl = sum(float(item["pnl"]) for item in closed if pd.Timestamp(item["updated_at"]) >= day_start)
        weekly_pnl = sum(float(item["pnl"]) for item in closed if pd.Timestamp(item["updated_at"]) >= week_start)
        consecutive_losses = 0
        for item in sorted(closed, key=lambda row: row["updated_at"], reverse=True):
            if float(item["pnl"]) < 0: consecutive_losses += 1
            else: break
        limits = self.limits()
        reasons: list[str] = []
        if self.store.get_setting("trading_disabled", False): reasons.append("Trading is disabled by the emergency switch.")
        if daily_pnl <= -(equity * limits.max_daily_loss_pct / 100): reasons.append("Daily loss limit reached.")
        if weekly_pnl <= -(equity * limits.max_weekly_drawdown_pct / 100): reasons.append("Weekly drawdown limit reached.")
        if open_risk >= equity * limits.max_total_open_risk_pct / 100: reasons.append("Maximum simultaneous open risk reached.")
        if consecutive_losses >= limits.max_consecutive_losses: reasons.append("Maximum consecutive losses reached.")
        return {
            "allowed": not reasons, "reasons": reasons, "daily_pnl": daily_pnl,
            "weekly_pnl": weekly_pnl, "open_risk": open_risk,
            "consecutive_losses": consecutive_losses, "limits": asdict(limits),
        }


class ForecastPlatform:
    def __init__(self, engine: ForecastEngine, store: PlatformStore) -> None:
        self.engine = engine
        self.store = store
        self._lock = threading.RLock()

    def generate(self, params: ForecastParameters, *, advanced: bool = False, paths: int | None = None) -> dict[str, Any]:
        canonical = normalize_symbol(params.symbol)
        started = time.perf_counter()
        history, projection = self.engine.forecast(params)
        uncertainty = None
        previous = self.store.forecasts(canonical, params.timeframe, 2)
        if advanced:
            path_count = max(3, min(int(paths or self.store.get_setting("uncertainty_paths", 7)), 25))
            frames = [projection]
            for _ in range(path_count - 1):
                _, sample = self.engine.forecast(ForecastParameters(**{**asdict(params), "sample_count": 1}))
                frames.append(sample)
            uncertainty = uncertainty_summary(frames)
            projection = pd.DataFrame(uncertainty["median"])
            projection["timestamp"] = pd.to_datetime(projection["timestamp"], utc=True)
        revision = revision_metrics(previous[0]["projection"], _rows(projection)) if previous else None
        elapsed = (time.perf_counter() - started) * 1000
        forecast_id = self.store.save_forecast(
            symbol=canonical, timeframe=params.timeframe, model_id=self.engine.settings.model_id,
            tokenizer_id=self.engine.settings.tokenizer_id, parameters=asdict(params), history=history,
            projection=projection, source=self.engine.provider.name, inference_ms=elapsed,
            uncertainty=uncertainty, revision=revision,
        )
        return {
            "id": forecast_id, "symbol": canonical, "timeframe": params.timeframe,
            "generated_at": utc_now_iso(), "history": _rows(history), "projection": _rows(projection),
            "uncertainty": uncertainty, "revision": revision, "advanced": advanced,
            "inference_ms": elapsed,
        }

    def score_market(self, symbol: str, timeframe: str, limit: int = 2500) -> int:
        actual = self.engine.candles(symbol, timeframe, limit)
        return self.store.score_realized(symbol, timeframe, actual)

    def consensus(self, symbol: str, selected_timeframe: str) -> dict[str, Any]:
        order = list(TIMEFRAMES)
        index = order.index(selected_timeframe)
        choices = sorted({order[max(0, index - 1)], selected_timeframe, order[min(len(order) - 1, index + 1)]}, key=order.index)
        readings: list[dict[str, Any]] = []
        for timeframe in choices:
            latest = self.store.forecasts(symbol, timeframe, 1)
            if not latest:
                readings.append({"timeframe": timeframe, "direction": "unknown", "move_pct": None})
                continue
            forecast = latest[0]
            base = float(forecast["history"][-1]["close"])
            end = float(forecast["projection"][-1]["close"])
            move = (end - base) / base * 100 if base else 0
            readings.append({"timeframe": timeframe, "direction": _direction(move), "move_pct": move, "forecast_id": forecast["id"]})
        known = [row["direction"] for row in readings if row["direction"] != "unknown"]
        agreement = max((known.count(value) for value in set(known)), default=0) / len(known) * 100 if known else 0
        return {"selected": selected_timeframe, "readings": readings, "agreement_pct": agreement, "consensus": max(set(known), key=known.count) if known else "unknown"}

    def cross_market_context(self) -> dict[str, Any]:
        contexts = []
        for symbol in ("XAUUSD", "XAGUSD", "NAS100", "SPX500"):
            latest = self.store.forecasts(symbol, self.engine.settings.default_timeframe, 1)
            if not latest:
                continue
            item = latest[0]
            base = float(item["history"][-1]["close"]); end = float(item["projection"][-1]["close"])
            contexts.append({"symbol": symbol, "move_pct": (end - base) / base * 100 if base else 0, "direction": _direction(end - base), "forecast_id": item["id"]})
        relationships = []
        by_symbol = {row["symbol"]: row for row in contexts}
        for first, second in (("XAUUSD", "XAGUSD"), ("NAS100", "SPX500")):
            if first in by_symbol and second in by_symbol:
                relationships.append({"pair": f"{first}/{second}", "aligned": by_symbol[first]["direction"] == by_symbol[second]["direction"], "difference_pct": by_symbol[first]["move_pct"] - by_symbol[second]["move_pct"]})
        return {"markets": contexts, "relationships": relationships}

    def replay(self, symbol: str, timeframe: str, start_index: int = 400, steps: int = 100, pred_len: int = 12) -> dict[str, Any]:
        canonical = normalize_symbol(symbol)
        candles = self.engine.candles(canonical, timeframe, min(5000, start_index + steps + pred_len + 10))
        if len(candles) < start_index + pred_len + 1:
            raise ValueError("Not enough candles are available for this replay window.")
        # A deterministic no-leak baseline uses recent drift/range. Full Kronos walk-forward
        # is intentionally opt-in because each replay step is a real model inference.
        records = []
        equity = 10000.0
        peak = equity
        max_drawdown = 0.0
        wins = 0
        for cursor in range(start_index, min(len(candles) - pred_len, start_index + steps)):
            train = candles.iloc[:cursor]
            actual = candles.iloc[cursor : cursor + pred_len]
            returns = train["close"].pct_change().tail(20).dropna()
            expected = float(returns.mean()) if len(returns) else 0.0
            direction = 1 if expected >= 0 else -1
            realized = (float(actual["close"].iloc[-1]) - float(train["close"].iloc[-1])) / float(train["close"].iloc[-1])
            trade_return = direction * realized
            equity *= 1 + trade_return
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
            wins += int(trade_return > 0)
            records.append({"timestamp": pd.Timestamp(actual["timestamp"].iloc[0]).isoformat(), "expected_direction": "bullish" if direction > 0 else "bearish", "realized_return_pct": realized * 100, "strategy_return_pct": trade_return * 100, "equity": equity})
        returns = [row["strategy_return_pct"] for row in records]
        return {"symbol": canonical, "timeframe": timeframe, "steps": len(records), "starting_equity": 10000, "ending_equity": equity, "return_pct": (equity / 10000 - 1) * 100, "win_rate_pct": wins / len(records) * 100 if records else 0, "max_drawdown_pct": max_drawdown, "mean_trade_pct": statistics.fmean(returns) if returns else 0, "records": records}


def _score_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "samples": len(rows),
        "direction_accuracy": sum(int(row["direction_correct"]) for row in rows) / len(rows) * 100,
        "mean_close_error_pct": statistics.fmean(float(row["close_error_pct"]) for row in rows),
        "range_hit_rate": sum(int(row["range_hit"]) for row in rows) / len(rows) * 100,
        "mean_high_error": statistics.fmean(float(row["high_error"]) for row in rows),
        "mean_low_error": statistics.fmean(float(row["low_error"]) for row in rows),
    }


def uncertainty_summary(frames: Sequence[pd.DataFrame]) -> dict[str, Any]:
    if not frames:
        raise ValueError("At least one forecast path is required.")
    length = min(len(frame) for frame in frames)
    keys = ("open", "high", "low", "close", "volume", "amount")
    median_rows: list[dict[str, Any]] = []
    lower_rows: list[dict[str, Any]] = []
    upper_rows: list[dict[str, Any]] = []
    outer_lower: list[dict[str, Any]] = []
    outer_upper: list[dict[str, Any]] = []
    bullish_probabilities: list[float] = []
    for index in range(length):
        timestamp = pd.Timestamp(frames[0].iloc[index]["timestamp"]).isoformat()
        distributions = {key: sorted(float(frame.iloc[index][key]) for frame in frames) for key in keys}
        def percentile(values: Sequence[float], value: float) -> float:
            if len(values) == 1: return values[0]
            position = (len(values) - 1) * value
            lower = math.floor(position); upper = math.ceil(position)
            return values[lower] if lower == upper else values[lower] + (values[upper] - values[lower]) * (position - lower)
        median_rows.append({"timestamp": timestamp, **{key: percentile(values, 0.5) for key, values in distributions.items()}})
        lower_rows.append({"timestamp": timestamp, **{key: percentile(values, 0.25) for key, values in distributions.items()}})
        upper_rows.append({"timestamp": timestamp, **{key: percentile(values, 0.75) for key, values in distributions.items()}})
        outer_lower.append({"timestamp": timestamp, **{key: percentile(values, 0.10) for key, values in distributions.items()}})
        outer_upper.append({"timestamp": timestamp, **{key: percentile(values, 0.90) for key, values in distributions.items()}})
        bullish_probabilities.append(sum(float(frame.iloc[index]["close"]) >= float(frame.iloc[index]["open"]) for frame in frames) / len(frames) * 100)
    widths = [max(0.0, upper_rows[index]["close"] - lower_rows[index]["close"]) for index in range(length)]
    return {"paths": len(frames), "median": median_rows, "p25": lower_rows, "p75": upper_rows, "p10": outer_lower, "p90": outer_upper, "bullish_probability": bullish_probabilities, "confidence_decay": [100 * math.exp(-index / max(length * 0.75, 1)) for index in range(length)], "mean_iqr_width": statistics.fmean(widths) if widths else 0}


def revision_metrics(previous: Sequence[dict[str, Any]], current: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not previous or not current:
        return {"available": False}
    shared = min(len(previous), len(current))
    previous = list(previous[:shared]); current = list(current[:shared])
    prior_start = float(previous[0]["open"]); current_start = float(current[0]["open"])
    prior_move = (float(previous[-1]["close"]) - prior_start) / max(abs(prior_start), 1e-12) * 100
    current_move = (float(current[-1]["close"]) - current_start) / max(abs(current_start), 1e-12) * 100
    differences = [abs(float(current[index]["close"]) - float(previous[index]["close"])) / max(abs(float(previous[index]["close"])), 1e-12) * 100 for index in range(shared)]
    prior_ranges = [float(row["high"]) - float(row["low"]) for row in previous]
    current_ranges = [float(row["high"]) - float(row["low"]) for row in current]
    similarity = max(0.0, 100 - statistics.fmean(differences) * 100)
    direction_flip = _direction(prior_move) != _direction(current_move)
    severity_score = min(100.0, statistics.fmean(differences) * 200 + abs(current_move - prior_move) * 10 + (35 if direction_flip else 0))
    severity = "major" if severity_score >= 60 else "moderate" if severity_score >= 25 else "minor"
    prior_peak = max(range(shared), key=lambda index: abs(float(previous[index]["close"]) - prior_start))
    current_peak = max(range(shared), key=lambda index: abs(float(current[index]["close"]) - current_start))
    return {
        "available": True, "direction_previous": _direction(prior_move), "direction_active": _direction(current_move),
        "direction_flip": direction_flip, "move_previous_pct": prior_move, "move_active_pct": current_move,
        "magnitude_change_pct_points": current_move - prior_move, "path_similarity_pct": similarity,
        "timing_shift_candles": current_peak - prior_peak,
        "volatility_change_pct": (statistics.fmean(current_ranges) / max(statistics.fmean(prior_ranges), 1e-12) - 1) * 100,
        "stability_score": similarity, "severity": severity, "severity_score": severity_score,
        "candle_consensus_pct": sum(_direction(float(previous[index]["close"]) - float(previous[index]["open"])) == _direction(float(current[index]["close"]) - float(current[index]["open"])) for index in range(shared)) / shared * 100,
    }


def _direction(value: float, threshold: float = 1e-9) -> str:
    if value > threshold: return "bullish"
    if value < -threshold: return "bearish"
    return "sideways"
