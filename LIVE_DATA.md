# Traid live data, forecasting, and execution

Traid supports **XAUUSD, XAGUSD, NAS100, and SPX500** using a broker-exact MT5 feed or Massive cloud data. Trading requires MT5; Massive is for charting/forecasting only.

## Data flow

1. Read completed OHLCV candles from the selected provider.
2. Read the freshest quote and, for MT5, the still-forming candle.
3. Feed only completed candles into Kronos so inputs do not repaint on every tick.
4. Stream quotes/current candles independently from model inference.
5. Persist every generated forecast in SQLite.
6. Score each forecast as its target candles become available.
7. Queue another inference when a candle closes during an existing inference.

The dashboard overlays:

- historical market candles or close-price line;
- current active candle/price;
- active Kronos projection;
- previous projection at 67%;
- older projection at 33%;
- projected volume;
- optional Advanced-mode uncertainty bounds.

A changed forecast eases from the currently rendered path to the updated path over **333 ms**. Forecast history resets when symbol or timeframe changes.

## Advanced Forecast mode

Advanced mode is toggleable in the chart toolbar. It performs multiple sampled Kronos runs and adds:

- median forecast path;
- 25/75 and 10/90 percentile paths;
- bullish probability and horizon confidence decay;
- direction-flip, magnitude, timing, volatility, similarity, stability, and consensus metrics;
- multi-timeframe and cross-market context.

These metrics never alter MT5 positions automatically.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-live.txt
Copy-Item .env.example .env
python -m traid_live.cli serve --host 127.0.0.1 --port 8000
```

Dashboard:

```powershell
python -m http.server 3000 -d dashboard
```

Open `http://localhost:3000`; API docs are at `http://localhost:8000/docs`.

## Main market endpoints

```text
GET  /health
GET  /v1/symbols
GET  /v1/quote/{symbol}?timeframe=5m
GET  /v1/candles/{symbol}?timeframe=5m&limit=400
POST /v1/forecast
GET  /v1/forecasts/{symbol}?timeframe=5m
GET  /v1/forecasts/id/{forecast_id}
POST /v1/forecasts/{symbol}/score
GET  /v1/forecast-context/{symbol}?timeframe=5m
WS   /v1/stream/{symbol}?timeframe=5m&with_forecast=true&advanced=false
```

Forecast example:

```bash
curl -X POST http://localhost:8000/v1/forecast \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"XAUUSD",
    "timeframe":"5m",
    "lookback":400,
    "pred_len":24,
    "sample_count":5,
    "advanced":true,
    "uncertainty_paths":7
  }'
```

## Platform endpoints

```text
POST /v1/auth/login
POST /v1/auth/logout
GET  /v1/platform/settings
PUT  /v1/platform/settings/{key}
GET  /v1/calendar
POST /v1/calendar
POST /v1/calendar/refresh
POST /v1/replay
GET  /v1/journal
POST /v1/journal
PATCH /v1/journal/{id}
GET  /v1/audit
```

## Trading endpoints

Authenticated administrator access is required for execution-changing actions.

```text
GET    /v1/trading/status
GET    /v1/trading/positions
POST   /v1/trading/risk-size
POST   /v1/trading/orders
POST   /v1/trading/pending
POST   /v1/trading/oco
DELETE /v1/trading/pending/{ticket}
POST   /v1/trading/positions/{ticket}/close
POST   /v1/trading/close-all
PUT    /v1/trading/positions/{ticket}
POST   /v1/trading/positions/{ticket}/break-even
PUT    /v1/trading/positions/{ticket}/trailing
PUT    /v1/trading/positions/{ticket}/smart-trailing
DELETE /v1/trading/positions/{ticket}/trailing
POST   /v1/trading/emergency-stop
POST   /v1/trading/emergency-resume
```

## Risk sizing

Traid calculates lots using account equity, requested risk percentage, stop distance, MT5 tick size/value, and broker volume steps. The risk engine also evaluates daily loss, weekly drawdown, simultaneous open risk, consecutive losses, and the emergency-disable state.

The custom spread/slippage/market-condition rejection gate discussed during planning is intentionally omitted. The broker's own MT5 validation remains in place.

## Trailing behavior

- **Fixed:** price-unit distance behind Bid/Ask.
- **Percent:** percentage of current price.
- **ATR:** multiple of recent true range.
- **Candle:** recent candle low for buys or high for sells.

The latest accepted Stop Loss exists at the broker. Application-managed trailing continues only while Traid, MT5, and the network remain available.

## Economic calendar

Set `TRAID_CALENDAR_URL` to a JSON endpoint returning either a list or `{ "events": [...] }`. Each event accepts:

```json
{
  "starts_at":"2026-08-01T12:30:00Z",
  "currency":"USD",
  "impact":"high",
  "title":"Employment report",
  "source":"provider-name"
}
```

Events can also be entered manually through the dashboard/API.

## Limitations

- Broker CFD prices differ from cash indices and other providers.
- Spot metals are decentralized.
- CFD volume may be tick volume.
- Kronos does not inherently know breaking news.
- Advanced uncertainty uses repeated stochastic inference and is slower than Basic mode.
- Replay is a decision-workflow tool, not proof of future profitability.
- Live broker behavior must be validated on the intended demo terminal before use.
