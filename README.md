# Traid

**Traid is a local-first, live forecast-overlay trading terminal built around MetaTrader 5, FastAPI, SQLite, and the Kronos financial time-series model.** It keeps broker quotes and the forming candle moving independently from model inference, overlays a genuine sampled Kronos trajectory on the market chart, evaluates forecasts as candles realize, adds ICT/Smart-Money-style market context, and places explicitly authorized MT5 orders behind persistent risk controls.

Traid currently supports:

- **Metals:** `XAUUSD`, `XAGUSD`
- **Forex:** `EURUSD`, `USDJPY`
- **Indices:** `NAS100`, `SPX500`
- **Timeframes:** `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`
- **Market data:** local MetaTrader 5 or Massive cloud data
- **Execution:** MetaTrader 5 only
- **Forecast model:** `NeoQuasar/Kronos-small` by default, with compatible Kronos models configurable through `.env`

> Traid provides probabilistic market information and guarded execution tooling. It is not investment advice, does not guarantee future prices, and never submits, closes, reverses, resizes, or modifies a position merely because a forecast or ICT context changes.

## Current system at a glance

| Area | Current behavior |
|---|---|
| Live market feed | Quote and forming-candle updates run independently from forecasting and scoring. |
| Multi-tab behavior | Browser tabs share one backend market channel and one forecast job per matching stream configuration. |
| Basic forecast | At least 10 genuine Kronos sampled paths are generated. |
| Advanced forecast | At least 14 genuine sampled paths plus uncertainty bands and full path metadata. |
| Displayed projection | One real sampled path selected from the ensemble; no hand-authored continuation curve. |
| Forecast cadence | One forecast identity per forming candle and configuration, with same-candle cache reuse. |
| Top-down context | `1h` directional bias → `15m` setup/location → `5m` entry trigger. |
| ICT/SMC context | Structure, BOS/CHoCH, liquidity, sweeps, FVGs, order blocks, dealing range, displacement, sessions, event risk. |
| Confidence | Context-matched realized calibration; no percentage until enough independent forecasts exist. |
| Adaptive learning | A compact classifier begins learning from realized outcomes after sufficient ICT-tagged forecasts. |
| Execution | Disabled by default; paper mode first; explicit authentication, confirmation, risk approval, and MT5 validation. |
| Persistence | SQLite/WAL stores forecasts, scores, settings, journal, events, audit entries, and execution metadata. |
| Application identity | Optional Firebase Google/phone accounts remain separate from the privileged MT5 operator session. |

## Live-first market architecture

Traid no longer makes the chart wait for Kronos.

The current runtime separates the work into independent paths:

1. **Live quote loop** polls the provider and publishes the newest quote quickly.
2. **Forming-candle loop** keeps the current OHLC candle aligned to the exact quote in the same WebSocket payload.
3. **Completed-candle worker** detects timeframe boundaries and backfills completed history.
4. **Forecast worker** performs Kronos inference without blocking quote updates.
5. **Scoring worker** evaluates realized forecast horizons separately.
6. **Shared channel** fans one market stream out to all matching browser tabs.

For MT5, provider calls are serialized process-wide because the official Python bridge shares terminal state. Multiple browser tabs therefore do not create independent competing MT5 pollers or duplicate GPU jobs.

The shared stream automatically recovers after temporary provider failures. Closed WebSockets and browser disconnects are handled without repeatedly sending into an already-closed connection.

### Live-candle safeguards

The dashboard and backend now guard against several chart problems that appeared during development:

- a quote entering a new timeframe bucket immediately starts a new live candle;
- the live candle close is always replaced by the exact quote from the same payload;
- out-of-order current-candle timestamps are ignored;
- a forming candle at or before the last completed candle is rejected;
- the completed candle is processed before the new current candle;
- the prior live-series point is cleared when its candle completes;
- completed history is periodically backfilled if a gap appears before the live candle;
- stale state is based on the stream heartbeat rather than whether price happened to change;
- repeated identical stream errors are throttled in the browser.

## Supported markets

| Traid symbol | Asset | Massive mapping | Notes |
|---|---|---|---|
| `XAUUSD` | Gold / U.S. dollar | `C:XAUUSD` | Broker CFD/spot-metal pricing can differ by venue. |
| `XAGUSD` | Silver / U.S. dollar | `C:XAGUSD` | Volume may be broker tick volume. |
| `EURUSD` | Euro / U.S. dollar | `C:EURUSD` | Added to the backend registry and dashboard runtime. |
| `USDJPY` | U.S. dollar / Japanese yen | `C:USDJPY` | Added to the backend registry and dashboard runtime. |
| `NAS100` | Nasdaq-100 CFD/context | `I:NDX` | Massive is a cash-index reference, not the broker CFD. |
| `SPX500` | S&P 500 CFD/context | `I:SPX` | Massive is a cash-index reference, not the broker CFD. |

The static dashboard shell contains the original four markets. `live-first-loader.js` adds `EURUSD` and `USDJPY` to the selector and watchlist during startup and patches their display names into the current dashboard application source.

Use the exact Market Watch symbol supplied by the broker. Every supported market has an optional alias environment variable:

```dotenv
TRAID_XAUUSD_SYMBOL=XAUUSD
TRAID_XAGUSD_SYMBOL=XAGUSD
TRAID_EURUSD_SYMBOL=EURUSD
TRAID_USDJPY_SYMBOL=USDJPY
TRAID_NAS100_SYMBOL=NAS100
TRAID_SPX500_SYMBOL=SPX500
```

Broker suffixes such as `.a`, `.pro`, `_ECN`, or `.cash` belong on the right side of those values.

## Kronos forecast pipeline

### Input construction

For each forecast, Traid:

1. loads the configured completed-candle lookback;
2. reads the current forming candle;
3. optionally appends one snapshot of that forming candle to the model input;
4. keeps completed history and the intrabar snapshot separately identified in stored metadata;
5. generates future timestamps through the active provider calendar;
6. runs a batched autoregressive Kronos decode;
7. enforces valid OHLC relationships and non-negative volume/amount;
8. ranks the genuine sampled paths;
9. persists the selected projection and full analytical metadata;
10. broadcasts the result without interrupting the live quote stream.

The intrabar signature is the forming candle's timestamp, not every tick. Price can continue moving while the same forecast is reused. A new forecast identity begins when a new forming candle begins or another identity component changes.

For `NAS100` and `SPX500`, `volume` and `amount` are zeroed before model inference and the forecast is marked `price_only`. Metals and forex use the normal OHLCV input path supplied by the provider.

### Sampling modes

The current intelligence runtime enforces these minimums:

- **Basic:** 10 sampled paths
- **Advanced:** 14 sampled paths

`TRAID_UNCERTAINTY_PATHS` remains configurable between 3 and 25 for compatibility, but persisted values below the current Advanced minimum are upgraded to 14 by the runtime.

Advanced mode stores and exposes:

- every sampled path;
- pointwise median values;
- 25th/75th percentile bands;
- 10th/90th percentile bands;
- per-horizon bullish probability;
- directional-vote counts and agreement;
- mean interquartile width;
- path-ranking components;
- forecast-revision analytics;
- market, ICT, gate, hierarchy, and confidence metadata.

### Real sampled trajectory integrity

Traid does **not** draw a smooth synthetic continuation when the forecast conflicts with the market context.

Earlier aggregation logic could produce a pointwise median assembled from different paths or a hand-authored momentum continuation. The current runtime instead:

- keeps all Kronos samples;
- selects one complete sampled trajectory;
- marks `projection_is_real_sample=true`;
- records the selected path index;
- filters obsolete synthetic-fallback forecasts from normal forecast-history reads;
- uses the context gate only to determine whether the setup is tradable.

The uncertainty bands may aggregate the ensemble, but the main projected candles are one path Kronos actually sampled.

## ICT and Smart-Money context

Traid now includes a deterministic ICT/SMC-style context engine derived from available OHLCV and event data. It is an analytical proxy, not access to proprietary ICT data or a guarantee that every discretionary chart interpretation will match a human trader.

### Market structure

The engine detects and stores:

- local swing highs and lows;
- higher-high/higher-low and lower-high/lower-low sequences;
- bullish or bearish break of structure (`BOS`);
- change of character (`CHoCH`) when the break opposes the prior sequence;
- EMA-derived fallback bias when swing evidence is incomplete;
- structure state, direction, strength, and latest swing prices.

### Liquidity

The engine builds potential buy-side and sell-side liquidity from:

- recent swing highs and lows;
- equal highs and equal lows within an ATR-aware tolerance;
- previous-day high and low;
- previous-week high and low;
- Asian, London, and New York session highs and lows.

It identifies a sweep when price trades through a level and closes back on the opposite side. It also selects the nearest directional liquidity draw used when ranking projected paths.

### Imbalances and setup location

Traid derives:

- active bullish and bearish fair-value gaps;
- a recent opposite candle as an order-block proxy when displacement exists;
- a recent dealing range;
- premium, discount, or equilibrium location;
- displacement direction, body ratio, range-to-ATR magnitude, and score;
- setup state: `waiting`, `developing`, `ready`, or `blocked_event`.

### Sessions and killzones

Session windows use the `America/New_York` IANA timezone when available, so daylight-saving changes are handled automatically:

- Asian reference window: 8:00 PM–12:00 AM New York time
- London window: 2:00 AM–5:00 AM New York time
- New York window: 7:00 AM–10:00 AM New York time

London and New York windows are marked as killzones. On Windows environments without timezone data, Traid remains bootable with a fixed UTC-5 fallback, but DST precision is lost.

### Economic-event gate

High-impact events are matched to the currencies relevant to the symbol:

- forex pairs use both component currencies;
- metals and the two index symbols use USD context.

The engine inspects nearby events and marks the ICT setup blocked inside its high-impact blackout window. This affects the context/trade gate only; it does not submit or cancel an order automatically.

## Top-down hierarchy

Traid generates context in this order:

1. **1h — directional bias**
2. **15m — setup and location**
3. **5m — entry trigger**

When a `5m` forecast is requested, the runtime first ensures current `1h` and `15m` ICT forecasts exist. A `15m` request first ensures current `1h` context exists. The selected path is therefore ranked with fresh higher-timeframe context before it is displayed.

The hierarchy requires, among other checks:

- a directional `1h` structure/model bias;
- a matching `15m` setup with sufficient quality;
- a matching `5m` sweep, displacement, BOS, or CHoCH trigger;
- no high-impact event block;
- no strong structure conflict;
- acceptable ensemble support;
- all relevant regime gates to allow the setup.

A failed hierarchy returns `trade_bias=no_trade` and an alignment state such as:

- `event_block`
- `conflict`
- `no_htf_bias`
- `waiting_15m_setup`
- `waiting_5m_trigger`
- `aligned`

This is a decision-context result. It does not itself place a trade.

## ICT-ranked path selection

Every genuine sampled path receives a weighted score using:

- directional support within the path ensemble;
- proximity to the ensemble's median trajectory;
- market-structure alignment;
- progress toward the identified liquidity objective;
- interaction with a matching fair-value gap or order block;
- premium/discount behavior;
- early displacement quality;
- volatility/magnitude plausibility.

The engine prefers a context-aligned path only when enough sampled paths support that direction. It records the complete ranking and the selected path's component scores.

The trade gate can still block the selected forecast when:

- the economic-event window is active;
- the context classifier favors `no_trade` strongly enough;
- the path conflicts with strong local structure;
- the path conflicts with current higher-timeframe structure;
- the setup is still waiting;
- ensemble support is too low.

The projected candles remain visible for analysis even when the gate says not to trade.

## Context model and confidence

### Heuristic context model

Before enough realized samples exist, Traid builds bullish, bearish, and no-trade probabilities from deterministic evidence including:

- structure direction and strength;
- liquidity sweep direction;
- displacement;
- fair-value-gap and order-block presence;
- premium/discount location;
- session/killzone context;
- event risk.

### Adaptive classifier

After at least **45 independent realized ICT forecasts** exist for a symbol/timeframe/horizon and at least two outcome classes are present, Traid fits a compact class-balanced multinomial logistic model from its own stored results.

The adaptive feature vector includes:

- structure bias, strength, BOS, and CHoCH;
- sweep and displacement direction;
- dealing-range position;
- fair-value-gap and order-block flags;
- setup bias and quality;
- session and killzone;
- event block;
- ATR/relative volatility;
- heuristic bullish, bearish, and no-trade probabilities.

The learned probabilities are blended with the deterministic model rather than replacing it outright. The fitted model is cached and retrained when new scored data changes the training set.

### Realized confidence calibration

Displayed confidence is context-matched by:

- symbol;
- timeframe;
- forecast horizon;
- broad market regime;
- ICT structure bias;
- session.

At least **30 independent matched forecasts** are required before a percentage is shown. Until then, the UI displays calibration progress instead of presenting an unsupported confidence number.

The calibrated score combines:

- direction accuracy: 60%
- distance accuracy: 25%
- range-hit accuracy: 15%

Calibration results are cached and invalidated when new realized forecast scores are inserted.

## Forecast identity, caching, and concurrency

A forecast identity includes the important generation inputs, including:

- symbol and timeframe;
- last completed candle;
- forming-candle timestamp/signature;
- model and tokenizer;
- lookback and prediction length;
- path count;
- temperature, top-k, and top-p;
- Basic or Advanced mode;
- relevant higher-timeframe context signatures.

Concurrent requests for the same identity share a generation lock. Same-candle requests reuse the persisted forecast. A selected-timeframe cache is invalidated when fresh higher-timeframe context no longer matches the context stored with the forecast.

The hierarchy consensus also reuses a fresh Advanced forecast rather than generating a redundant Basic forecast for the same selected candle.

## Dashboard and chart behavior

The dashboard remains intentionally compact. The ICT implementation did not add a separate wall of indicators or a new full-size pane.

The existing four forecast-context cards are reused as:

1. **Structure**
2. **Liquidity**
3. **Session / volatility**
4. **ICT alignment**

The current chart includes:

- completed market candles or a close-price line;
- a separate live forming candle;
- active forecast at full visual strength;
- previous forecast at approximately 45% visual strength;
- older forecast at approximately 22%;
- projected volume;
- Advanced uncertainty paths/bands;
- a pulsing gradient separator between `REAL` and `FORECAST`;
- hover dimming that emphasizes the side under the pointer;
- open-position entry arrows when the chart library supports markers;
- dashed entry-price lines with long/short labels.

Only the active forecast plus two prior generations are retained visually. A forecast update transitions over approximately 333 ms.

### Stabilized browser runtime

The browser currently starts through a layered runtime rather than loading `app.js` directly:

```text
index.html
  ├─ firebase-auth.js
  ├─ ict-ui-runtime.js
  └─ live-first-loader.js
       ├─ app-loader.js
       │    ├─ repairs supported legacy app.js syntax
       │    ├─ applies chart/request/WebSocket guards
       │    └─ loads forecast-intelligence.js
       └─ chart-enhancements-runtime.js
```

The stabilization layer adds:

- request IDs for asynchronous market loads;
- symbol/timeframe capture for every request;
- stale-response rejection after rapid market switching;
- timeframe-cadence validation for candles, projections, and bands;
- WebSocket instance and request-identity checks;
- protection against an old socket marking a new connection offline;
- live-candle monotonic timestamp checks;
- forecast-history de-duplication by input candle;
- cached forecast reuse before a new POST request;
- clipping of old projections to timestamps after the latest completed candle;
- startup error panels when required source markers cannot be patched.

The live stream is connected before forecast generation, so opening a market does not leave the chart frozen while Kronos works.

## Persistent forecast ledger and scoring

SQLite/WAL stores each forecast with:

- forecast ID;
- symbol and timeframe;
- generated time and last completed input timestamp;
- model and tokenizer IDs;
- provider/source;
- generation parameters;
- completed historical window;
- selected projected OHLCV path;
- full path ensemble and uncertainty data when available;
- forecast revision metrics;
- intrabar snapshot and signature;
- market and ICT context;
- hierarchy and regime gate;
- model/context confidence;
- inference duration.

As target candles become available, Traid scores each horizon using:

- close error and percentage close error;
- directional correctness;
- range hit;
- high error;
- low error;
- volume error when meaningful.

These realized scores feed the dashboard, contextual confidence, and adaptive ICT classifier.

## Risk-controlled MT5 execution

### Default posture

- Trading is disabled by default.
- Trading mode defaults to `paper`.
- Paper requests run MT5 `order_check` but do not call `order_send`.
- Live mode requires explicit server configuration and explicit request confirmation.

### Supported actions

- market buy/sell;
- buy/sell limit;
- buy/sell stop;
- OCO pending groups;
- cancel pending orders;
- partial or full close;
- close all Traid-managed positions;
- direct Stop Loss/Take Profit modification;
- break-even moves;
- fixed trailing;
- percentage trailing;
- ATR trailing;
- candle-high/low trailing.

Traid manages only positions and orders using its configured MT5 magic number.

### Risk controls

Persistent controls include:

- risk-per-trade percentage;
- required Stop Loss;
- maximum order lots;
- maximum open positions;
- maximum positions per symbol;
- maximum daily loss;
- maximum weekly drawdown;
- maximum simultaneous open risk;
- maximum consecutive losses;
- emergency trading disable/resume;
- optional close-all.

Risk sizing uses equity, requested percentage, stop distance, tick size, tick value, broker volume minimum/maximum/step, and Traid's maximum-lot setting.

The custom spread/slippage/market-condition rejection gate discussed during planning is intentionally **not** implemented. MT5 broker validation still applies, but Traid does not apply a custom rejection rule based on spread, volatility, rollover, cross-feed disagreement, or forecast state.

## Authentication boundaries

Traid has two deliberately separate identity systems.

### FastAPI operator session

The local administrator/trader session protects account and execution-changing endpoints. Configure either a plaintext development password or a PBKDF2 hash:

```dotenv
TRAID_ADMIN_USER=admin
TRAID_ADMIN_PASSWORD=replace_with_a_strong_password
# TRAID_ADMIN_PASSWORD_HASH=pbkdf2_sha256$...
TRAID_SESSION_TTL_SECONDS=28800
```

Generate a hash:

```powershell
python -c "from traid_live.auth import SessionAuth; print(SessionAuth.hash_password('replace_me'))"
```

When `TRAID_TRADING_ENABLED=true`, the current configuration validator also requires a long random `TRAID_TRADING_API_KEY`, even when the dashboard normally authenticates through an operator session.

### Firebase application account

Firebase provides optional user-facing application identity through Google or phone sign-in. It does not grant operator/trader privileges.

Each first sign-in creates one private Firestore profile at:

```text
users/{firebaseAuthUid}
```

The immutable public Traid ID format is:

```text
YYMMDD + 14 uppercase letters/numbers
```

Firestore rules prevent users from listing profiles, reading another user's profile, changing the public ID/creation time, assigning a role, deleting the record, or writing elsewhere.

See [FIREBASE.md](FIREBASE.md) for the Hosting target, Auth providers, emulator ports, rules, and deployment commands.

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

Keep MetaTrader 5 open and logged into the intended account before starting the service.

### One-command launcher

Install the `traid` PowerShell command into the current user's PowerShell profile:

```powershell
.\install-traid-command.ps1
```

Available commands:

```powershell
traid
traid start
traid start -NoBrowser
traid status
traid restart
traid stop
```

The launcher:

- starts the FastAPI backend on `127.0.0.1:8000`;
- starts the dashboard on `127.0.0.1:3000`;
- stores process IDs in the gitignored `.traid-runtime.json` file;
- waits for the backend and dashboard health checks;
- opens the dashboard unless `-NoBrowser` is supplied;
- can display port and HTTP health status;
- stops only the processes recorded in its runtime state.

### Manual startup

Backend:

```powershell
python -m traid_live.cli serve --host 127.0.0.1 --port 8000
```

Dashboard in a second terminal:

```powershell
python -m http.server 3000 -d dashboard
```

Open:

```text
http://127.0.0.1:3000
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

Health endpoint:

```text
http://127.0.0.1:8000/health
```

## Minimum MT5 configuration

```dotenv
TRAID_PROVIDER=mt5

TRAID_XAUUSD_SYMBOL=XAUUSD
TRAID_XAGUSD_SYMBOL=XAGUSD
TRAID_EURUSD_SYMBOL=EURUSD
TRAID_USDJPY_SYMBOL=USDJPY
TRAID_NAS100_SYMBOL=NAS100
TRAID_SPX500_SYMBOL=SPX500
```

Optional explicit terminal login values remain backend-only:

```dotenv
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=replace_me
MT5_SERVER=Broker-Server
```

Never place MT5 credentials in dashboard JavaScript or commit a real `.env` file.

## Forecast configuration

Default model configuration:

```dotenv
TRAID_MODEL_ID=NeoQuasar/Kronos-small
TRAID_TOKENIZER_ID=NeoQuasar/Kronos-Tokenizer-base
TRAID_MAX_CONTEXT=512
TRAID_TIMEFRAME=5m
TRAID_LOOKBACK=400
TRAID_PRED_LEN=24
TRAID_ADVANCED_FORECAST_DEFAULT=false
TRAID_UNCERTAINTY_PATHS=14
# TRAID_DEVICE=cuda:0
```

A compatible larger model can be selected, for example:

```dotenv
TRAID_MODEL_ID=NeoQuasar/Kronos-base
TRAID_TOKENIZER_ID=NeoQuasar/Kronos-Tokenizer-base
```

Larger models and Advanced path counts increase latency and memory use. A CUDA-capable GPU is strongly preferred for interactive forecasting.

## Feed cadence and persistence

```dotenv
TRAID_DATABASE_PATH=data/traid.db
TRAID_QUOTE_POLL_SECONDS=0.5
TRAID_BAR_POLL_SECONDS=2
TRAID_CALENDAR_REFRESH_MINUTES=15
# TRAID_CALENDAR_URL=https://example.com/calendar.json
```

The live-priority runtime caps the interactive quote loop at 0.25 seconds when the configured poll value is faster than the normal service cadence allows, while the completed-candle worker follows `TRAID_BAR_POLL_SECONDS`.

## Paper trading first

```dotenv
TRAID_TRADING_ENABLED=true
TRAID_TRADING_MODE=paper
TRAID_TRADING_API_KEY=replace_with_a_long_random_secret
TRAID_REQUIRE_STOP_LOSS=true
TRAID_MAX_ORDER_LOTS=1.0
TRAID_MAX_OPEN_POSITIONS=4
TRAID_MAX_POSITIONS_PER_SYMBOL=1
```

Paper mode performs broker preflight without sending the order. Validate the entire workflow on an MT5 demo account before changing:

```dotenv
TRAID_TRADING_MODE=live
```

Live entries and closes also require explicit `confirm_live=true` from the dashboard or API.

## Massive cloud fallback

```dotenv
TRAID_PROVIDER=massive
MASSIVE_API_KEY=replace_me
MASSIVE_BASE_URL=https://api.massive.com
```

Massive is for charting and forecasting. Live execution currently requires `TRAID_PROVIDER=mt5`.

## Firebase setup

The configured default Firebase project alias and Hosting target are `keitraid`.

Local emulators:

```bash
npm install -g firebase-tools
firebase login
firebase use keitraid
firebase emulators:start
```

The Hosting emulator normally runs at `http://127.0.0.1:5000`; Auth uses `9099`, and Firestore uses `8080`.

Deploy:

```bash
firebase deploy --only hosting:keitraid,firestore
```

When the dashboard is not served by Firebase Hosting or its emulator, copy:

```text
dashboard/firebase-config.example.js
dashboard/firebase-config.local.js
```

and fill in the Firebase web values. The local file is gitignored.

## Main API surface

### Market and forecast

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

### Platform

```text
POST  /v1/auth/login
POST  /v1/auth/logout
GET   /v1/platform/settings
PUT   /v1/platform/settings/{key}
GET   /v1/calendar
POST  /v1/calendar
POST  /v1/calendar/refresh
POST  /v1/replay
GET   /v1/journal
POST  /v1/journal
PATCH /v1/journal/{id}
GET   /v1/audit
```

### Trading

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

Execution-changing endpoints require the appropriate authenticated role and server-side trading configuration.

## Runtime stack

The CLI serves `traid_live.final_runtime:app`. That module intentionally composes the runtime layers in order:

```text
service_runtime.py
  Base patched FastAPI application and aligned forecast service

multitab_runtime.py
  Serialized MT5 I/O, one generation per identity, shared tab channels

multitab_resilience.py
  Shared-publisher recovery after provider errors

intelligence_v2.py
  Intrabar input, batched samples, path metadata, scoring, calibration hooks

trajectory_integrity.py
  Genuine sampled-path enforcement and synthetic-history filtering

ict_runtime.py
  ICT context, path ranking, gates, hierarchy, contextual confidence

ict_runtime_patch.py
  Adaptive classifier, DST session patch, hierarchy-aware cache, calibration cache

ict_consensus_runtime.py
  Fresh 1h/15m/5m hierarchy without duplicate selected-timeframe inference

live_priority_runtime.py
  Independent quote publishing and exact live-candle close
```

## Project structure

```text
traid_live/
  service.py                    Core FastAPI routes and workers
  service_patch.py              Confidence/alignment and safe stream patches
  service_runtime.py            Fresh aligned runtime bootstrap
  final_runtime.py              Final composed application entry point
  multitab_runtime.py           Shared market channels and MT5 serialization
  multitab_resilience.py        Shared-stream recovery
  live_priority_runtime.py      Quote-first stream and live candle
  intelligence_v2.py            Batched Kronos path generation and metadata
  trajectory_integrity.py       Real sampled projection enforcement
  ict_context.py                Structure/liquidity/FVG/OB/session/event engine
  ict_sessions.py               DST-aware New York session windows
  ict_learning.py               Adaptive context classifier
  ict_runtime.py                ICT ranking, gates, confidence, hierarchy
  ict_runtime_patch.py          Cache/learning/session integration
  ict_consensus_runtime.py      Efficient hierarchy consensus
  forecast.py                   Model loading and base forecast engine
  platform.py                   SQLite, scoring, risk, journal, events, replay
  trading.py                    Guarded market execution and fixed trailing
  advanced_trading.py           Pending/OCO, modify, break-even, smart trailing
  providers/                    MT5 and Massive adapters
  auth.py                       Operator sessions and rate limiting

dashboard/
  index.html                    Compact responsive shell
  app.js                        Main dashboard application source
  app-loader.js                 Runtime stabilization and source patching
  live-first-loader.js          Stream-first startup, forex injection, visuals
  forecast-intelligence.js      Confidence and context rendering
  chart-enhancements-runtime.js Forecast boundary, entries, continuity guard
  ict-ui-runtime.js             ICT data mapped into the existing four cards
  firebase-auth.js              Google/phone auth and private user profile
  app.css                       Desktop/tablet/mobile styling
tests/
  test_live_data.py             Provider/service behavior
  test_platform.py              Persistence/risk/platform behavior
  test_ict_context.py           Structure, sweep handling, and event gating
docs/
  ARCHITECTURE.md
  FORECASTING.md
  TRADING.md
  BACKTESTING.md
  SECURITY.md
  DEPLOYMENT.md
  UPSTREAM_KRONOS.md
traid.ps1                       Start/stop/restart/status launcher
install-traid-command.ps1       PowerShell profile installer
FIREBASE.md                     Firebase Auth/Firestore/Hosting setup
LIVE_DATA.md                    API and live-data reference
```

## Validation

Static and deterministic checks:

```powershell
python -m compileall -q traid_live
python -m pytest -q tests/test_live_data.py tests/test_platform.py tests/test_ict_context.py
node --check dashboard/app.js
node --check dashboard/app-loader.js
node --check dashboard/live-first-loader.js
node --check dashboard/forecast-intelligence.js
node --check dashboard/chart-enhancements-runtime.js
node --check dashboard/ict-ui-runtime.js
```

A complete integration test additionally requires:

- Windows;
- the intended MetaTrader 5 terminal;
- valid broker symbols and credentials;
- downloaded Kronos model/tokenizer files;
- sufficient CPU/GPU memory;
- a live or demo market session when testing candle boundaries;
- browser testing of rapid symbol/timeframe switching;
- demo-account validation of order filling, stops, volume rules, and trailing.

## Known operational characteristics

- The first selected `5m` forecast may take longer because Traid builds current `1h` and `15m` context first.
- Advanced mode is slower because it generates and retains more sampled paths.
- The browser runtime patches `app.js` in memory; a source-marker mismatch produces a visible startup error rather than silently loading a partial dashboard.
- Massive cash-index values are context references and will not exactly match broker CFDs.
- Spot metals and forex are decentralized, and broker candles can differ.
- CFD volume may represent tick volume.
- Kronos and the ICT context engine do not inherently know breaking news unless it is represented in price or the configured calendar.
- Session and ICT detections are deterministic analytical proxies, not guaranteed discretionary setups.
- Application-managed trailing pauses while Traid, MT5, or the network is unavailable; the last broker-accepted Stop Loss remains at MT5.
- Replay and historical accuracy are evaluation tools, not proof of future profitability.

## Git history incorporated into this README

This README was reconciled against the current source and the **97 commits after the previous full README rewrite**, from:

```text
b6b5d79e82101b0f81416201dd44bd29a23bf582
Rewrite README for the Traid platform
```

through:

```text
875d6428706123eecf0ec2057742e1f4a384252b
Cache ICT calibration and remove duplicate metadata work
```

The reviewed change groups include:

- complete architecture, forecasting, trading, replay, security, deployment, API, and upstream-attribution documentation;
- Firebase Hosting, Google/phone Auth, Firestore profiles/rules, and emulator support;
- complete local `.env` configuration and Firebase local routing;
- dashboard startup repairs and chart-library failure handling;
- Kronos timestamp normalization;
- asynchronous request, WebSocket, timeframe, and live-candle stabilization;
- safe WebSocket disconnect handling;
- one-command PowerShell launcher and installer;
- increased Basic/Advanced sampling and contextual confidence;
- fresh `5m`/`15m`/`1h` alignment;
- shared multi-tab streams and provider recovery;
- intrabar path-voting intelligence;
- removal of synthetic forecast arcs in favor of real sampled trajectories;
- quote-first streaming independent of inference;
- `EURUSD` and `USDJPY` backend/dashboard support;
- exact timeframe-boundary live-candle resets;
- `REAL`/`FORECAST` boundary visuals and position-entry visuals;
- completed-history continuity backfill;
- deterministic ICT/SMC context;
- ICT-ranked genuine path selection and trade gating;
- top-down `1h`/`15m`/`5m` forecast generation;
- DST-aware sessions and killzones;
- adaptive context learning;
- context-aware forecast caching;
- duplicate-inference removal;
- contextual-calibration caching and invalidation.

Temporary one-time validator/cleanup workflows that were later removed are historical implementation details and are not described as runtime features.

## Documentation

- [Live data and API](LIVE_DATA.md)
- [Firebase setup](FIREBASE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Forecasting](docs/FORECASTING.md)
- [Trading and risk](docs/TRADING.md)
- [Backtesting and replay](docs/BACKTESTING.md)
- [Security](docs/SECURITY.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Upstream Kronos project](docs/UPSTREAM_KRONOS.md)

## License and upstream model

Traid is built on the open-source Kronos financial candlestick foundation model. Upstream attribution is preserved in [docs/UPSTREAM_KRONOS.md](docs/UPSTREAM_KRONOS.md). See [LICENSE](LICENSE).
