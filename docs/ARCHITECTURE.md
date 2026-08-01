# Architecture

## Runtime components

```text
Responsive browser dashboard
        │ HTTPS / WebSocket
        ▼
FastAPI service (traid_live.service)
  ├─ session authentication and rate limiting
  ├─ market-data streaming
  ├─ forecast queue and scoring
  ├─ risk and execution API
  ├─ calendar/replay/journal API
  └─ background OCO/trailing/calendar worker
        │
        ├─ SQLite WAL (forecasts, scores, journal, settings, audit)
        ├─ Kronos model/tokenizer
        └─ Provider
             ├─ local Windows MT5 terminal
             └─ Massive cloud fallback
```

## Separation of responsibilities

- `forecast.py` performs Kronos inference from completed candles.
- `platform.py` owns durable analytics, scoring, risk state, replay, events, and journals.
- `trading.py` owns guarded MT5 market execution and fixed trailing.
- `advanced_trading.py` owns pending/OCO orders, SL/TP changes, break-even, and smart trailing.
- `auth.py` provides private-deployment sessions and rate limiting.
- `service.py` coordinates APIs, WebSockets, and workers.
- `dashboard/` is a responsive, chart-first client.

## Forecast lifecycle

1. A completed candle is detected.
2. Any previous forecast job continues; a second request is marked queued rather than dropped.
3. Kronos generates Basic or Advanced output.
4. Output and metadata are persisted before broadcast.
5. The dashboard retains three generations and animates active OHLCV values over 333 ms.
6. Realized candles score matching forecast horizons.

## Execution boundary

Forecasting and execution remain separate. No revision, confidence, accuracy, event, or context value directly triggers an order. An authenticated administrator submits an explicit request, and the risk layer plus MT5 preflight must allow it.

## Production scaling

SQLite/WAL is appropriate for a private single-service deployment. For multiple API instances, migrate persistence to PostgreSQL, sessions/jobs to Redis, and model inference to a dedicated worker queue. Keep the Windows MT5 bridge on a private network segment.
