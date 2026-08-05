from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

import httpx
import pandas as pd
from fastapi import Query

from .config import SUPPORTED_SYMBOLS
from .service import app, store


FREE_CALENDAR_URL = os.getenv(
    "TRAID_FREE_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
).strip()
FREE_CALENDAR_ENABLED = os.getenv("TRAID_FREE_CALENDAR_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# The public export is updated about hourly and rate-limited. Never poll it more
# frequently than once per hour from Traid.
FREE_CALENDAR_REFRESH_SECONDS = max(
    3600,
    int(os.getenv("TRAID_FREE_CALENDAR_REFRESH_MINUTES", "60")) * 60,
)
FREE_CALENDAR_SOURCE = "Forex Factory public calendar"
TRACKED_IMPACTS = {"medium", "high"}
TRACKED_CURRENCIES = {"USD", "EUR", "JPY", "ALL"}

_refresh_lock = asyncio.Lock()
_last_refresh_monotonic = 0.0
_last_refreshed_at: str | None = None
_last_error: str | None = None
_calendar_task: asyncio.Task[Any] | None = None


def _affected_symbols(currency: str, title: str) -> list[str]:
    del title
    code = currency.upper()
    if code == "USD":
        # Every currently supported Traid market either contains USD directly or
        # is a USD-denominated index/metal CFD.
        return list(SUPPORTED_SYMBOLS)
    if code == "EUR":
        return ["EURUSD"]
    if code == "JPY":
        return ["USDJPY"]
    if code == "ALL":
        return ["XAUUSD", "XAGUSD", "NAS100", "SPX500"]
    return []


def _event_id(currency: str, title: str, starts_at: pd.Timestamp) -> str:
    raw = f"fair-economy|{currency.upper()}|{title.strip()}|{starts_at.isoformat()}"
    return "ff-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = str(raw.get("title") or "").strip()
    currency = str(raw.get("country") or raw.get("currency") or "").upper().strip()
    impact = str(raw.get("impact") or "").lower().strip()
    if not title or currency not in TRACKED_CURRENCIES or impact not in TRACKED_IMPACTS:
        return None

    try:
        starts_at = pd.Timestamp(raw.get("date") or raw.get("starts_at"))
        starts_at = (
            starts_at.tz_localize("UTC")
            if starts_at.tzinfo is None
            else starts_at.tz_convert("UTC")
        )
    except Exception:
        return None

    affected = _affected_symbols(currency, title)
    if not affected:
        return None

    return {
        "id": _event_id(currency, title, starts_at),
        "starts_at": starts_at.isoformat(),
        "currency": currency,
        "impact": impact,
        "title": title,
        "source": FREE_CALENDAR_SOURCE,
        "actual": raw.get("actual") or None,
        "forecast": raw.get("forecast") or None,
        "previous": raw.get("previous") or None,
        "metadata": {
            "affected_symbols": affected,
            "calendar_currency": currency,
            "source_kind": "public_weekly_export",
            "source_url": FREE_CALENDAR_URL,
            "directional_signal": False,
        },
    }


def _replace_auto_events(events: list[dict[str, Any]]) -> None:
    with store._lock, store.connection() as connection:
        connection.execute(
            "DELETE FROM economic_events WHERE source=?",
            (FREE_CALENDAR_SOURCE,),
        )
    for event in events:
        store.upsert_event(event, actor="free-calendar-sync")


async def refresh_free_calendar(*, force: bool = False) -> int:
    global _last_refresh_monotonic, _last_refreshed_at, _last_error
    if not FREE_CALENDAR_ENABLED or not FREE_CALENDAR_URL:
        return 0

    now = time.monotonic()
    if not force and _last_refresh_monotonic and now - _last_refresh_monotonic < FREE_CALENDAR_REFRESH_SECONDS:
        return 0

    async with _refresh_lock:
        now = time.monotonic()
        if not force and _last_refresh_monotonic and now - _last_refresh_monotonic < FREE_CALENDAR_REFRESH_SECONDS:
            return 0

        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Traid/1.0 economic-calendar sync",
                },
            ) as client:
                response = await client.get(FREE_CALENDAR_URL)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("The free calendar feed did not return a JSON list.")

            events = [event for raw in payload if (event := _normalize_event(raw))]
            await asyncio.to_thread(_replace_auto_events, events)
            _last_refresh_monotonic = time.monotonic()
            _last_refreshed_at = pd.Timestamp.now(tz="UTC").isoformat()
            _last_error = None
            return len(events)
        except Exception as exc:
            _last_refresh_monotonic = time.monotonic()
            _last_error = str(exc)
            return 0


async def _calendar_loop() -> None:
    while True:
        try:
            await refresh_free_calendar()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(FREE_CALENDAR_REFRESH_SECONDS)


@app.on_event("startup")
async def start_free_calendar_runtime() -> None:
    global _calendar_task
    if not FREE_CALENDAR_ENABLED:
        return
    await refresh_free_calendar(force=True)
    _calendar_task = asyncio.create_task(_calendar_loop())


@app.on_event("shutdown")
async def stop_free_calendar_runtime() -> None:
    global _calendar_task
    if _calendar_task:
        _calendar_task.cancel()
        try:
            await _calendar_task
        except asyncio.CancelledError:
            pass
        _calendar_task = None


@app.get("/v1/calendar/live")
async def live_calendar(
    start: str | None = None,
    end: str | None = None,
    impact: str | None = None,
    symbol: str | None = Query(default=None),
) -> dict[str, Any]:
    await refresh_free_calendar()
    events = store.events(start, end, impact)
    selected = str(symbol or "").upper().strip()
    if selected:
        events = [
            event
            for event in events
            if selected in ((event.get("metadata") or {}).get("affected_symbols") or [])
        ]
    return {
        "events": events,
        "symbol": selected or None,
        "source": FREE_CALENDAR_SOURCE,
        "source_url": FREE_CALENDAR_URL,
        "source_configured": FREE_CALENDAR_ENABLED,
        "last_refreshed_at": _last_refreshed_at,
        "refresh_error": _last_error,
    }
