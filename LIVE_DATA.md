# Traid live market data

Traid can now pull completed candles for **XAUUSD, XAGUSD, NAS100, and SPX500** and pass them directly into Kronos for forward projections.

## Why the default is MT5 instead of TradingView

TradingView does not expose its displayed market data through a general public data API. Its charting libraries expect you to bring your own datafeed. Also, `NAS100` and `SPX500` are usually broker-defined CFD products; their prices, trading sessions, spreads, and symbol names can differ from the cash `NDX` and `SPX` indices.

For a forecast intended to match trades placed at a broker, use the broker's own MT5 candles. The Massive provider is included as a cloud fallback and maps the index aliases to `I:NDX` and `I:SPX`.

## Data flow

1. Read the latest completed OHLC candles from MT5 or Massive.
2. Normalize them to `timestamp, open, high, low, close, volume, amount`.
3. Exclude the still-forming candle to prevent repainting.
4. Feed up to `TRAID_MAX_CONTEXT` candles into `KronosPredictor`.
5. Build valid future timestamps for the selected market and interval.
6. Decode the generated tokens into projected candles.
7. Repair impossible decoded candle relationships and clamp volume/amount to zero or greater.
8. Return historical and projected candles through REST, WebSocket, or the CLI.

## Install

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-live.txt
Copy-Item .env.example .env
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements-live.txt
cp .env.example .env
```

The official `MetaTrader5` Python package is installed only on Windows. For MT5 mode, keep the desktop terminal open and logged into the account whose prices you want Traid to follow.

## Configure MT5

Set:

```dotenv
TRAID_PROVIDER=mt5
```

The defaults assume the symbols are named exactly:

```dotenv
TRAID_XAUUSD_SYMBOL=XAUUSD
TRAID_XAGUSD_SYMBOL=XAGUSD
TRAID_NAS100_SYMBOL=NAS100
TRAID_SPX500_SYMBOL=SPX500
```

Many brokers add suffixes. Examples include `XAUUSD.a`, `USTEC`, `US100.cash`, `US500`, or `SPX500.pro`. Replace each value with the exact symbol shown in your MT5 Market Watch.

You can use the terminal's active login without storing credentials. Optional connection values are documented in `.env.example`.

## Configure Massive fallback

```dotenv
TRAID_PROVIDER=massive
MASSIVE_API_KEY=replace_me
```

Default mappings:

| Traid symbol | Massive ticker | Meaning |
| --- | --- | --- |
| XAUUSD | `C:XAUUSD` | Spot gold quoted in USD |
| XAGUSD | `C:XAGUSD` | Spot silver quoted in USD |
| NAS100 | `I:NDX` | Nasdaq-100 cash index |
| SPX500 | `I:SPX` | S&P 500 cash index |

Cloud access and recency depend on the market-data products enabled for the API key. The service returns a clear provider error when a ticker or interval is not included.

## Run the API

```bash
python -m traid_live.cli serve --host 0.0.0.0 --port 8000
```

Interactive API docs:

```text
http://localhost:8000/docs
```

### Health and supported symbols

```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/symbols
```

### Latest completed candles

```bash
curl "http://localhost:8000/v1/candles/XAUUSD?timeframe=5m&limit=400"
```

### Generate a projection

```bash
curl -X POST http://localhost:8000/v1/forecast \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "XAUUSD",
    "timeframe": "5m",
    "lookback": 400,
    "pred_len": 24,
    "temperature": 1.0,
    "top_p": 0.9,
    "sample_count": 5
  }'
```

The response contains both `history` and `projection`, ready for a frontend candlestick series.

### Stream each newly completed candle

```text
ws://localhost:8000/v1/stream/XAUUSD?timeframe=5m
```

To regenerate the projection when a new candle closes:

```text
ws://localhost:8000/v1/stream/XAUUSD?timeframe=5m&with_forecast=true&pred_len=24
```

Forecasting is intentionally triggered on completed bars, not every tick. This produces stable inputs and avoids repeatedly running a large model while the active candle is still changing.

## CLI projection

```bash
python -m traid_live.cli forecast \
  --symbol NAS100 \
  --timeframe 5m \
  --lookback 400 \
  --pred-len 24 \
  --sample-count 5 \
  --output nas100-forecast.json
```

## Supported intervals

`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `1d`.

Kronos-small and Kronos-base use a maximum context of 512 candles by default. Increasing `lookback` beyond the configured context does not give the model additional history.

## TradingView display integration

You can display the API results with TradingView Lightweight Charts or another frontend chart library. The chart should call Traid's REST endpoint for initial history and then subscribe to the WebSocket endpoint for each newly completed bar. TradingView Advanced Charts can also consume the same backend, but it requires access to TradingView's private charting-library repository and a custom Datafeed API adapter.

## Important limitations

- A cash-index feed is not identical to a broker's CFD price.
- Spot metals are decentralized and provider quotes can differ.
- Index volume is often unavailable and is represented as zero.
- Kronos does not inherently know breaking news or macroeconomic releases.
- Generated candles are probabilistic scenarios, not guaranteed future prices or automatic trade instructions.
