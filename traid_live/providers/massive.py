from __future__ import annotations

import math
from datetime import timedelta

import httpx
import pandas as pd

from ..config import Settings
from ..market import INDEX_SYMBOLS, MASSIVE_SYMBOLS, get_timeframe, normalize_symbol
from .base import CandleProvider, MarketDataError, MarketQuote


class MassiveProvider(CandleProvider):
    """Cloud OHLC and snapshot provider for spot metals and cash indices."""

    name = "massive"

    def __init__(self, settings: Settings):
        if not settings.massive_api_key:
            raise MarketDataError("MASSIVE_API_KEY is required for the Massive provider.")
        self.api_key = settings.massive_api_key
        self.base_url = settings.massive_base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        canonical = normalize_symbol(symbol)
        tf = get_timeframe(timeframe)
        ticker = MASSIVE_SYMBOLS[canonical]

        now = pd.Timestamp.now(tz="UTC")
        raw_days = math.ceil((limit * tf.seconds) / 86400)
        multiplier = 5 if canonical in INDEX_SYMBOLS else 3
        horizon_days = max(30, raw_days * multiplier + 14)
        if timeframe == "1d":
            horizon_days = max(horizon_days, limit * 3)
        start = (now - pd.Timedelta(days=horizon_days)).date().isoformat()
        end = now.date().isoformat()

        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}/range/"
            f"{tf.massive_multiplier}/{tf.massive_timespan}/{start}/{end}"
        )
        response = self.client.get(
            url,
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": self.api_key,
            },
        )
        if response.status_code >= 400:
            raise MarketDataError(
                f"Massive request failed for {ticker}: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )

        payload = response.json()
        if payload.get("status") not in {None, "OK"}:
            raise MarketDataError(
                f"Massive returned an error for {ticker}: {payload.get('error') or payload}"
            )
        results = payload.get("results") or []
        if not results:
            raise MarketDataError(
                f"Massive returned no bars for {ticker}. Confirm the ticker is included in "
                "your plan and override the mapping if your account uses another instrument."
            )

        frame = pd.DataFrame(results).rename(
            columns={
                "t": "timestamp",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            }
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        if "volume" not in frame:
            frame["volume"] = 0.0
        frame["amount"] = frame["volume"].fillna(0.0) * frame[
            ["open", "high", "low", "close"]
        ].mean(axis=1)

        # REST aggregates can include the currently forming bar. Remove it so the
        # model only consumes finalized inputs.
        completed_before = now - pd.Timedelta(seconds=2)
        frame = frame[
            frame["timestamp"] + pd.Timedelta(seconds=tf.seconds) <= completed_before
        ]
        return self.normalize_frame(frame, limit)

    def get_quote(self, symbol: str) -> MarketQuote:
        canonical = normalize_symbol(symbol)
        ticker = MASSIVE_SYMBOLS[canonical]
        asset_type = "indices" if canonical in INDEX_SYMBOLS else "fx"
        response = self.client.get(
            f"{self.base_url}/v3/snapshot",
            params={
                "ticker": ticker,
                "type": asset_type,
                "limit": 1,
                "apiKey": self.api_key,
            },
        )
        if response.status_code >= 400:
            raise MarketDataError(
                f"Massive snapshot failed for {ticker}: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )

        payload = response.json()
        results = payload.get("results") or []
        if not results:
            return super().get_quote(canonical)
        result = results[0]
        if result.get("error"):
            raise MarketDataError(
                f"Massive snapshot error for {ticker}: "
                f"{result.get('message') or result.get('error')}"
            )

        last_quote = result.get("last_quote") or result.get("lastQuote") or {}
        bid_raw = last_quote.get("bid_price", last_quote.get("bid"))
        ask_raw = last_quote.get("ask_price", last_quote.get("ask"))
        bid = float(bid_raw) if bid_raw is not None else None
        ask = float(ask_raw) if ask_raw is not None else None

        price_raw = result.get("value")
        if price_raw is None and bid is not None and ask is not None:
            price_raw = (bid + ask) / 2
        if price_raw is None:
            session = result.get("session") or {}
            price_raw = session.get("close")
        if price_raw is None:
            return super().get_quote(canonical)

        timestamp_raw = (
            result.get("last_updated")
            or last_quote.get("participant_timestamp")
            or last_quote.get("sip_timestamp")
        )
        if timestamp_raw:
            timestamp = pd.to_datetime(int(timestamp_raw), unit="ns", utc=True)
        else:
            timestamp = pd.Timestamp.now(tz="UTC")

        timeframe = str(result.get("timeframe") or "").upper()
        return MarketQuote(
            symbol=canonical,
            timestamp=timestamp,
            price=float(price_raw),
            bid=bid,
            ask=ask,
            spread=(ask - bid) if bid is not None and ask is not None else None,
            delayed=timeframe != "REAL-TIME" if timeframe else None,
            source=f"massive:{ticker}",
        )

    def future_timestamps(
        self,
        symbol: str,
        timeframe: str,
        last_timestamp: pd.Timestamp,
        periods: int,
    ) -> pd.DatetimeIndex:
        canonical = normalize_symbol(symbol)
        if canonical not in INDEX_SYMBOLS:
            return super().future_timestamps(canonical, timeframe, last_timestamp, periods)

        tf = get_timeframe(timeframe)
        last = pd.Timestamp(last_timestamp)
        last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")

        if timeframe == "1d":
            output: list[pd.Timestamp] = []
            current = last
            while len(output) < periods:
                current += pd.Timedelta(days=1)
                if current.weekday() < 5:
                    output.append(current)
            return pd.DatetimeIndex(output)

        try:
            import pandas_market_calendars as mcal
        except ImportError as exc:
            raise MarketDataError(
                "pandas-market-calendars is required to build cash-index sessions."
            ) from exc

        calendar = mcal.get_calendar("NYSE")
        end_date = (last + timedelta(days=max(30, periods // 20 + 14))).date()
        schedule = calendar.schedule(start_date=last.date(), end_date=end_date)

        output: list[pd.Timestamp] = []
        for _, session in schedule.iterrows():
            market_open = pd.Timestamp(session["market_open"]).tz_convert("UTC")
            market_close = pd.Timestamp(session["market_close"]).tz_convert("UTC")
            starts = pd.date_range(
                start=market_open,
                end=market_close - pd.Timedelta(seconds=tf.seconds),
                freq=tf.pandas_freq,
                tz="UTC",
            )
            for timestamp in starts:
                if timestamp > last:
                    output.append(timestamp)
                    if len(output) == periods:
                        return pd.DatetimeIndex(output)

        raise MarketDataError("Could not construct enough future index timestamps.")
