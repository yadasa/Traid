# Traid dashboard

Start the API from the repository root:

```bash
python -m traid_live.cli serve --port 8000
```

In another terminal, serve this directory:

```bash
python -m http.server 3000 -d dashboard
```

Open `http://localhost:3000`.

## What the dashboard shows

The ultra-dark blue interface uses TradingView Lightweight Charts only as the renderer. All data comes from the Traid backend:

- green/red completed market candles;
- a cyan still-forming MT5 candle;
- a live quote line, bid, ask, spread, and connection status;
- blue/purple Kronos projection candles overlaid on the same price/time axis;
- market and projected volume on the lower chart scale;
- automatic projection refresh after each completed candle;
- account balance/equity, Traid-managed positions, and a protected order ticket.

Open the settings drawer to set:

- the Traid backend URL;
- the separate `TRAID_TRADING_API_KEY` value;
- the number of candles to project.

The API URL, trading key, and projection length are stored only in browser `sessionStorage`, so they are cleared when the browser session ends. MT5 credentials never enter the dashboard.

Trading remains disabled unless the backend explicitly has `TRAID_TRADING_ENABLED=true`. Paper mode is strongly recommended until the complete workflow has been tested against an MT5 demo account.
