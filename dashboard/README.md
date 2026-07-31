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

The page uses TradingView Lightweight Charts only for rendering. Historical candles, newly completed candles, and Kronos projections all come from the Traid API. The blue candles are projected values; green/red candles are completed provider bars.
