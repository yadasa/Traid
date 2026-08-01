# Traid

**Traid is a live forecast-overlay trading terminal for XAUUSD, XAGUSD, NAS100, and SPX500.** It combines broker-exact MetaTrader 5 candles and quotes with Kronos OHLCV projections, persistent forecast evaluation, account-risk controls, journaling, replay, economic-event context, and guarded MT5 execution.

The interface is optimized for both desktop and mobile:

- **Desktop:** watchlist, chart/forecast workspace, analytics, account risk, and order ticket remain visible together.
- **Tablet:** the order ticket moves below the chart without losing controls.
- **Mobile:** chart-first layout, touch-sized controls, safe-area support, bottom navigation, slide-out watchlist, and a bottom-sheet order ticket.

> Traid provides probabilistic market information and execution tooling. It is not investment advice, does not guarantee prices, and does not automatically trade merely because a forecast changes.

## Core capabilities

### Live market and forecast overlay

- Broker-exact MT5 quotes and completed/active candles.
- Massive cloud fallback for charting and forecasting.
- Historical OHLCV, the current forming candle, and Kronos projections on one chart.
- Candlestick and line-chart modes.
- Exactly three displayed forecast generations:
  - active forecast at full visual strength;
  - previous forecast at 67% opacity/saturation;
  - older forecast at 33%;
  - no older forecasts remain visible.
- A 333 ms eased transition from the most recently rendered forecast to each updated forecast.
- Forecast jobs are queued if another candle closes while inference is still running.

### Basic and Advanced Forecast modes

**Basic mode** displays the active path, two prior paths, live market data, and historical accuracy.

**Advanced Forecast mode is explicitly toggleable** and adds the more analytical revision features:

- multiple sampled Kronos paths;
- median projection;
- 25–75% and 10–90% uncertainty ranges;
- bullish probability and confidence decay by horizon;
- bullish/bearish direction-flip detection;
- magnitude and timing changes;
- forecast-volatility changes;
- path similarity, stability, and candle consensus;
- multi-timeframe consensus;
- cross-market context.

Advanced revision analytics are informational. They never close, reverse, resize, or modify an existing trade.

### Persistent forecast ledger and evaluation

SQLite/WAL persistence records:

- model and tokenizer version;
- symbol, timeframe, provider, and generation timestamp;
- input window and forecast parameters;
- complete predicted OHLCV path;
- uncertainty and revision analytics;
- inference time.

As real candles arrive, Traid scores forecasts by horizon using directional accuracy, close error, high/low error, range-hit rate, and volume error.

### Risk-controlled MT5 execution

- Trading disabled by default.
- Paper mode by default.
- Broker `order_check` validation.
- Required Stop Loss by default.
- Risk-percentage position sizing using MT5 tick size/value and volume rules.
- Maximum daily loss, weekly drawdown, simultaneous open risk, and consecutive-loss controls.
- Emergency trading disable/resume and optional close-all.
- Market, limit, and stop orders.
- OCO pending-order groups.
- Partial/full closing.
- Direct SL/TP modification.
- Break-even moves.
- Fixed, percentage, ATR, and candle-high/low trailing methods.
- Durable client-order idempotency and audit history.
- MT5 magic-number ownership prevents Traid from managing unrelated positions.

The previously proposed **spread/slippage/market-condition rejection gate is intentionally not implemented**, per project direction. MT5 broker validation still applies, but Traid does not reject trades based on a custom spread, volatility, rollover, or cross-feed threshold.

### Workflow and analysis

- Economic-calendar import through a configurable JSON endpoint, plus manual events.
- Upcoming-event display and context.
- Multi-timeframe and cross-market forecast context.
- Historical replay with no future-candle leakage in the replay window.
- Automatic and manual trading journal entries.
- Forecast attached to each journaled entry.
- Audit log for settings, forecasts, orders, journal changes, and emergency actions.
- Browser notifications for meaningful forecast direction flips.
- Watchlist, stale-feed warning, saveable local layout/preferences, and keyboard shortcuts.

## Quick start on Windows

The official MetaTrader 5 Python package requires Windows and a locally installed MT5 terminal.

```powershell
git clone https://github.com/yadasa/Traid.git
cd Traid
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-live.txt
Copy-Item .env.example .env
```

Start the backend:

```powershell
python -m traid_live.cli serve --host 127.0.0.1 --port 8000
```

Start the dashboard in another terminal:

```powershell
python -m http.server 3000 -d dashboard
```

Open `http://localhost:3000`. API documentation is available at `http://localhost:8000/docs`.

## Minimum MT5 configuration

Keep MT5 running and logged into the intended account. Traid can use the account already active in the terminal.

```dotenv
TRAID_PROVIDER=mt5
TRAID_XAUUSD_SYMBOL=XAUUSD
TRAID_XAGUSD_SYMBOL=XAGUSD
TRAID_NAS100_SYMBOL=NAS100
TRAID_SPX500_SYMBOL=SPX500
```

Use the exact Market Watch names supplied by the broker, including suffixes such as `.a`, `.pro`, `_ECN`, or `.cash`.

Optional explicit terminal login values remain on the backend only:

```dotenv
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=replace_me
MT5_SERVER=Broker-Server
```

Never place MT5 credentials in dashboard JavaScript or commit a real `.env` file.

## Authentication

For a private local deployment, configure an administrator password or PBKDF2 hash:

```dotenv
TRAID_ADMIN_USER=admin
TRAID_ADMIN_PASSWORD=replace_with_a_strong_password
```

Generate a hash without storing the plaintext value:

```powershell
python -c "from traid_live.auth import SessionAuth; print(SessionAuth.hash_password('replace_me'))"
```

Then use:

```dotenv
TRAID_ADMIN_PASSWORD_HASH=pbkdf2_sha256$...
```

The legacy `TRAID_TRADING_API_KEY` remains supported for API clients, but dashboard sessions are preferred.

## Paper trading first

```dotenv
TRAID_TRADING_ENABLED=true
TRAID_TRADING_MODE=paper
TRAID_REQUIRE_STOP_LOSS=true
TRAID_MAX_ORDER_LOTS=1.0
TRAID_MAX_OPEN_POSITIONS=4
TRAID_MAX_POSITIONS_PER_SYMBOL=1
```

Paper mode runs MT5 preflight validation but does not call `order_send`. Validate the complete workflow on an MT5 demo account before changing:

```dotenv
TRAID_TRADING_MODE=live
```

Live entries and closes also require explicit `confirm_live=true` from the dashboard or API.

## Cloud market-data fallback

```dotenv
TRAID_PROVIDER=massive
MASSIVE_API_KEY=replace_me
```

Default mappings:

| Traid | Massive | Meaning |
|---|---|---|
| XAUUSD | `C:XAUUSD` | Spot gold/USD |
| XAGUSD | `C:XAGUSD` | Spot silver/USD |
| NAS100 | `I:NDX` | Nasdaq-100 cash index |
| SPX500 | `I:SPX` | S&P 500 cash index |

Cash indices are not identical to a broker's CFD products. MT5 remains the recommended source whenever forecasts and execution must match the broker.

## Project structure

```text
traid_live/
  service.py             FastAPI, WebSockets, workers, routes
  platform.py            SQLite forecast/risk/journal/replay platform
  advanced_trading.py    Pending/OCO orders and position management
  trading.py             Guarded MT5 market execution and fixed trailing
  forecast.py            Kronos inference
  providers/             MT5 and Massive data adapters
  auth.py                Local sessions and rate limiting
dashboard/
  index.html             Responsive semantic shell
  app.css                Desktop/tablet/mobile terminal layouts
  app.js                 Charting, forecasts, trading, replay, journal
docs/                     Architecture, security, deployment, forecasting
```

## Documentation

- [Live data and API](LIVE_DATA.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Forecasting](docs/FORECASTING.md)
- [Trading and risk](docs/TRADING.md)
- [Backtesting and replay](docs/BACKTESTING.md)
- [Security](docs/SECURITY.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Upstream Kronos project](docs/UPSTREAM_KRONOS.md)

## Validation

```bash
python -m compileall -q traid_live
python -m pytest -q tests/test_live_data.py tests/test_platform.py
node --check dashboard/app.js
```

Actual order fills, broker symbol aliases, contract specifications, stop distances, and filling policies must still be validated against the intended MT5 **demo** terminal and broker before live use.

## License and upstream model

Traid is built on the open-source Kronos financial candlestick foundation model. Upstream information and attribution are preserved in [docs/UPSTREAM_KRONOS.md](docs/UPSTREAM_KRONOS.md). See [LICENSE](LICENSE).
