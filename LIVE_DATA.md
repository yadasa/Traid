# Traid live market data and MT5 execution

Traid can ingest broker/cloud market data for **XAUUSD, XAGUSD, NAS100, and SPX500**, overlay a live active candle and quote on top of historical candles, and extend the same chart with Kronos-generated OHLCV projections.

When MT5 trading is explicitly enabled, Traid can also preflight and submit market orders, attach initial Stop Loss/Take Profit levels, list and close Traid-managed positions, and maintain application-controlled trailing stops.

## Why MT5 is the default instead of TradingView data

TradingView does not expose the prices displayed on its website through a general public market-data API. Its charting libraries expect the application to supply a datafeed. Also, `NAS100` and `SPX500` are usually broker-defined CFD products; their candles, sessions, spreads, and symbol names can differ from the cash `NDX` and `SPX` indices.

For forecasts and trades that must match your broker, use the broker's own MT5 feed. The Massive provider remains available as a cloud fallback for charting and forecasting, but trade execution requires MT5.

## Chart data flow

1. Read completed OHLCV candles from MT5 or Massive.
2. Read the freshest quote and, when available, the still-forming MT5 candle.
3. Normalize all candles to `timestamp, open, high, low, close, volume, amount`.
4. Feed only completed candles into `KronosPredictor`, preventing the model input from repainting on every tick.
5. Render completed candles, the active candle, and the Kronos projection on the same price/time scale.
6. Render historical and predicted volume on the lower overlay scale.
7. Stream live price/active-candle updates independently while a new projection is generated in the background after a candle closes.

The first projected candle intentionally occupies the same time slot as the currently active candle. This allows the dashboard to show the model's expectation and the real developing candle at the same time.

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

macOS/Linux can run the cloud provider and dashboard, but the official MetaTrader 5 Python package requires Windows and a local MT5 terminal:

```bash
source .venv/bin/activate
python -m pip install -r requirements-live.txt
cp .env.example .env
```

## Configure MT5 market data

```dotenv
TRAID_PROVIDER=mt5
TRAID_QUOTE_POLL_SECONDS=0.5
TRAID_BAR_POLL_SECONDS=2
```

Keep the desktop MT5 terminal running and logged in. Traid can use the account already active in the terminal, or you can configure the optional server-side connection values:

```dotenv
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=replace_me
MT5_SERVER=Broker-Server
```

These values must remain in `.env` on the backend. They are never entered into or returned to the browser.

Set the exact symbols shown in MT5 Market Watch:

```dotenv
TRAID_XAUUSD_SYMBOL=XAUUSD
TRAID_XAGUSD_SYMBOL=XAGUSD
TRAID_NAS100_SYMBOL=NAS100
TRAID_SPX500_SYMBOL=SPX500
```

Brokers may instead use names such as `GOLD`, `XAUUSD.a`, `USTEC`, `US100.cash`, `US500`, or `SPX500.pro`.

## Massive cloud fallback

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

Cloud access, real-time status, and volume availability depend on the products enabled for the API key. The application automatically uses a slower quote cadence for cloud REST snapshots.

## Run the API and dashboard

Backend:

```bash
python -m traid_live.cli serve --host 0.0.0.0 --port 8000
```

Dashboard:

```bash
python -m http.server 3000 -d dashboard
```

Open:

```text
http://localhost:3000
```

Interactive API documentation:

```text
http://localhost:8000/docs
```

The dashboard uses a dark navy/blue interface with purple forecast accents. It includes:

- completed historical candlesticks;
- a separately styled active candle;
- a live price line, bid, ask, and spread;
- Kronos OHLCV projection candles overlaid on the same timeline;
- projected and historical volume;
- symbol and timeframe controls;
- forecast refresh status;
- protected account information and open positions;
- a paper/live order ticket with Stop Loss, Take Profit, and trailing-stop controls.

## Market-data API

Latest quote and current candle:

```bash
curl "http://localhost:8000/v1/quote/XAUUSD?timeframe=5m"
```

Completed candles:

```bash
curl "http://localhost:8000/v1/candles/XAUUSD?timeframe=5m&limit=400"
```

Forecast:

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

Live WebSocket:

```text
ws://localhost:8000/v1/stream/XAUUSD?timeframe=5m&with_forecast=true&pred_len=24
```

The socket emits fast `market_update` messages and separate `projection_update` messages. The live chart therefore remains responsive while model inference runs.

## Enable protected MT5 trading

Trading is disabled by default. Start with a demo account and paper mode:

```dotenv
TRAID_PROVIDER=mt5
TRAID_TRADING_ENABLED=true
TRAID_TRADING_MODE=paper
TRAID_TRADING_API_KEY=replace_with_a_long_random_secret
TRAID_REQUIRE_STOP_LOSS=true
TRAID_MAX_ORDER_LOTS=1.0
TRAID_MAX_OPEN_POSITIONS=4
TRAID_MAX_POSITIONS_PER_SYMBOL=1
```

Enable AutoTrading in the MT5 desktop terminal. Then open the dashboard settings drawer and enter only the value of `TRAID_TRADING_API_KEY`. The MT5 login/password remain server-side.

Paper mode runs the broker's MT5 `order_check` validation but does not call `order_send`. After validating symbol names, volume steps, Stop Loss distances, filling policies, and the complete workflow on a demo account, live mode can be enabled:

```dotenv
TRAID_TRADING_MODE=live
```

Every live order/close request must also contain `confirm_live=true`, and the dashboard requires the live confirmation checkbox.

### Protected trading endpoints

All endpoints below require:

```text
X-Traid-Key: <TRAID_TRADING_API_KEY>
```

Status/account limits:

```bash
curl http://localhost:8000/v1/trading/status \
  -H "X-Traid-Key: replace_with_a_long_random_secret"
```

Open Traid-managed positions:

```bash
curl http://localhost:8000/v1/trading/positions \
  -H "X-Traid-Key: replace_with_a_long_random_secret"
```

Paper/live market order:

```bash
curl -X POST http://localhost:8000/v1/trading/orders \
  -H "Content-Type: application/json" \
  -H "X-Traid-Key: replace_with_a_long_random_secret" \
  -d '{
    "symbol": "XAUUSD",
    "side": "buy",
    "volume": 0.01,
    "stop_loss_distance": 5,
    "take_profit_distance": 10,
    "trailing_distance": 3,
    "trailing_activation": 3,
    "trailing_step": 0.5,
    "client_order_id": "unique-request-id",
    "confirm_live": false
  }'
```

Distances are expressed in the instrument's price units, not a universal pip definition. For example, a distance of `5` on XAUUSD means five dollars in the quoted gold price; a distance of `50` on NAS100 means fifty index-price units. Traid increases a requested distance when necessary to satisfy the broker's minimum stop level.

## Trailing-stop behavior

The initial Stop Loss is attached to the position and exists at the broker once accepted. Traid then polls the position and tick price, moving the Stop Loss only in the profitable direction when all conditions are met:

- `trailing_activation`: required favorable movement before trailing begins;
- `trailing_distance`: distance maintained behind the current Bid for buys or Ask for sells;
- `trailing_step`: minimum improvement required before another modification is sent;
- broker minimum stop distance and symbol precision are enforced.

Trailing configurations are persisted in `data/trailing_stops.json` and restored after an application restart. The file is ignored by Git.

A trailing stop is application/terminal-side logic, not a continuously moving server-side order. The backend and MT5 terminal must remain online for it to keep advancing. If they stop, the most recently placed Stop Loss remains at the broker and can still trigger, but it will not move again until Traid resumes.

## Built-in execution safeguards

- disabled-by-default trading;
- paper mode by default;
- protected trading endpoints using a separate API key;
- MT5 `order_check` preflight before any send;
- broker volume minimum, maximum, and step validation;
- broker minimum Stop Loss/Take Profit distance enforcement;
- filling-policy fallback across broker-supported modes;
- required Stop Loss by default;
- configurable lot and open-position limits;
- one open Traid-managed position per symbol by default;
- unique MT5 magic number so Traid does not expose or close unrelated manual positions;
- client order IDs to reduce duplicate submissions within the running service;
- explicit confirmation required for live entries and closes.

## Supported intervals

`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `1d`.

Kronos-small and Kronos-base use a maximum context of 512 candles by default. Increasing `lookback` beyond the configured context does not add history to those models.

## Important limitations

- A cash-index feed is not identical to a broker's CFD price.
- Spot metals are decentralized and provider quotes can differ.
- Index or CFD volume may be tick volume rather than centralized traded volume.
- Kronos does not inherently know breaking news, economic releases, or sudden liquidity changes.
- Predicted candles are probabilistic scenarios, not guaranteed prices or autonomous trade instructions.
- Stop orders may fill with slippage during gaps or fast markets.
- The Python service, Windows machine, MT5 terminal, network, and broker connection are all dependencies for application-managed trailing behavior.
