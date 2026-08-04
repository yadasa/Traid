from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Common ICT-style New York clock windows. These convert through IANA time zones,
# so daylight-saving transitions do not shift the chart context by an hour.
_SESSION_WINDOWS = {
    "asian": (time(20, 0), time(0, 0), -1),
    "london": (time(2, 0), time(5, 0), 0),
    "new_york": (time(7, 0), time(10, 0), 0),
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _window(
    timestamp: pd.Timestamp,
    name: str,
    *,
    day_offset: int = 0,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    utc = _utc_timestamp(timestamp)
    local = utc.tz_convert(NEW_YORK)
    start_clock, end_clock, start_day_adjustment = _SESSION_WINDOWS[name]
    base_date = local.date() + timedelta(days=day_offset)
    start_date = base_date + timedelta(days=start_day_adjustment)
    end_date = base_date
    start = pd.Timestamp(datetime.combine(start_date, start_clock), tz=NEW_YORK).tz_convert("UTC")
    end = pd.Timestamp(datetime.combine(end_date, end_clock), tz=NEW_YORK).tz_convert("UTC")
    if end <= start:
        end += pd.Timedelta(days=1)
    return start, end


def session_name(timestamp: pd.Timestamp) -> tuple[str, bool]:
    utc = _utc_timestamp(timestamp)
    for name in ("asian", "london", "new_york"):
        for day_offset in (0, 1):
            start, end = _window(utc, name, day_offset=day_offset)
            if start <= utc < end:
                return name, name in {"london", "new_york"}
    return "after_hours", False


def session_levels(data: pd.DataFrame) -> list[dict[str, Any]]:
    if data.empty:
        return []
    timestamp = _utc_timestamp(data.iloc[-1]["timestamp"])
    levels: list[dict[str, Any]] = []
    for name in ("asian", "london", "new_york"):
        candidates: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        for day_offset in (-1, 0, 1):
            start, end = _window(timestamp, name, day_offset=day_offset)
            if start < timestamp:
                candidates.append((start, min(end, timestamp)))
        if not candidates:
            continue
        start, end = max(candidates, key=lambda pair: pair[0])
        sample = data[(data["timestamp"] >= start) & (data["timestamp"] < end)]
        if sample.empty:
            continue
        levels.extend(
            [
                {
                    "type": f"{name}_high",
                    "side": "buy_side",
                    "price": float(sample["high"].max()),
                    "session_start": start.isoformat(),
                    "session_end": end.isoformat(),
                },
                {
                    "type": f"{name}_low",
                    "side": "sell_side",
                    "price": float(sample["low"].min()),
                    "session_start": start.isoformat(),
                    "session_end": end.isoformat(),
                },
            ]
        )
    return levels
