const HISTORICAL_REPLAY_HISTORY_VISIBLE = 140;
const HISTORICAL_REPLAY_DEFAULT_CUTOFF = 120;
const HISTORICAL_REPLAY_DEFAULT_HORIZON = 24;

let historicalReplayPayload = null;
let historicalReplayChart = null;
let historicalReplayHistorySeries = null;
let historicalReplayForecastSeries = null;
let historicalReplayForecastLine = null;
let historicalReplayActualSeries = null;
let historicalReplayIndex = 0;
let historicalReplayPlaying = false;
let historicalReplayToken = 0;

function historicalReplayRows(rows) {
  return (Array.isArray(rows) ? rows : []).map(row => ({
    ...row,
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
  }));
}

function historicalReplayDirection(base, price) {
  if (!Number.isFinite(base) || !Number.isFinite(price)) return '—';
  if (price > base) return 'Bullish';
  if (price < base) return 'Bearish';
  return 'Sideways';
}

function historicalReplayFormatPrice(value) {
  if (!Number.isFinite(Number(value))) return '—';
  const precision = typeof symbolPricePrecision === 'function'
    ? symbolPricePrecision(state.symbol, state.quote)
    : (Number(value) > 1000 ? 2 : 4);
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

function installHistoricalReplayStyles() {
  if (document.getElementById('historicalReplayStyles')) return;
  const style = document.createElement('style');
  style.id = 'historicalReplayStyles';
  style.textContent = `
    .historical-replay-shell { display:grid; gap:10px; }
    .historical-replay-copy { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
    .historical-replay-copy h3 { margin:0; font-size:13px; }
    .historical-replay-copy p { margin:4px 0 0; max-width:760px; color:var(--muted); font-size:9px; line-height:1.55; }
    .historical-replay-controls {
      display:grid; grid-template-columns:minmax(130px,.7fr) minmax(130px,.7fr) auto; gap:8px; align-items:end;
    }
    .historical-replay-controls label, .historical-replay-speed {
      display:grid; gap:4px; color:var(--muted); font-size:8px; font-weight:750; letter-spacing:.04em;
    }
    .historical-replay-controls input, .historical-replay-speed select { width:100%; }
    .historical-replay-transport {
      display:flex; align-items:end; flex-wrap:wrap; gap:7px; padding:8px 0 0; border-top:1px solid var(--line);
    }
    .historical-replay-transport .ghost-button { min-width:74px; }
    .historical-replay-speed { width:90px; }
    .historical-replay-progress { margin-left:auto; align-self:center; color:var(--muted); font-size:9px; font-variant-numeric:tabular-nums; }
    .historical-replay-chart-wrap { position:relative; height:270px; min-height:230px; border:1px solid var(--line); border-radius:11px; overflow:hidden; background:#070b1b; }
    #historicalReplayChart { position:absolute; inset:0; }
    .historical-replay-legend {
      position:absolute; z-index:4; left:10px; top:8px; display:flex; gap:10px; padding:5px 7px;
      border:1px solid rgba(139,158,213,.12); border-radius:7px; background:rgba(7,11,27,.78);
      color:var(--muted); font-size:8px; pointer-events:none;
    }
    .historical-replay-legend span { display:inline-flex; align-items:center; gap:4px; }
    .historical-replay-dot { width:10px; height:3px; border-radius:6px; display:inline-block; }
    .historical-replay-dot.history { background:#2dd4bf; }
    .historical-replay-dot.forecast { background:#8b5cf6; }
    .historical-replay-dot.actual { background:#fb7185; }
    .historical-replay-note {
      padding:8px 10px; border:1px solid rgba(139,158,213,.12); border-radius:9px;
      color:var(--muted); background:rgba(15,22,47,.35); font-size:8px; line-height:1.55;
    }
    .historical-replay-note strong { color:#cbd5e1; }
    @media (max-width:700px) {
      .historical-replay-controls { grid-template-columns:1fr 1fr; }
      .historical-replay-controls .primary-button { grid-column:1/-1; }
      .historical-replay-progress { width:100%; margin-left:0; }
    }
  `;
  document.head.appendChild(style);
}

function historicalReplayMarkup() {
  return `
    <div class="historical-replay-shell">
      <div class="historical-replay-copy">
        <div>
          <h3>Historical Kronos replay</h3>
          <p>Choose a historical cutoff. Kronos runs once using only completed candles available before that cutoff. Play then reveals the real completed candles that followed without running the model again.</p>
        </div>
      </div>
      <div class="historical-replay-controls">
        <label>Cutoff · candles ago
          <input id="historicalReplayCutoff" type="number" min="2" max="4500" value="${HISTORICAL_REPLAY_DEFAULT_CUTOFF}" />
        </label>
        <label>Projection candles
          <input id="historicalReplayHorizon" type="number" min="1" max="200" value="${HISTORICAL_REPLAY_DEFAULT_HORIZON}" />
        </label>
        <button class="primary-button" id="historicalReplayGenerate" type="button">Generate Kronos forecast</button>
      </div>
      <div class="historical-replay-transport">
        <button class="ghost-button" id="historicalReplayPlay" type="button" disabled>▶ Play</button>
        <button class="ghost-button" id="historicalReplayStep" type="button" disabled>Step candle</button>
        <button class="ghost-button" id="historicalReplayReset" type="button" disabled>Reset</button>
        <label class="historical-replay-speed">Playback speed
          <select id="historicalReplaySpeed">
            <option value="0.65">0.65×</option>
            <option value="1" selected>1×</option>
            <option value="2">2×</option>
            <option value="4">4×</option>
          </select>
        </label>
        <span id="historicalReplayProgress" class="historical-replay-progress">Generate a forecast to begin.</span>
      </div>
      <div class="stat-grid replay-stats">
        <article><span>Cutoff</span><strong id="historicalReplayCutoffStat">—</strong></article>
        <article><span>Model context</span><strong id="historicalReplayContextStat">—</strong></article>
        <article><span>Forecast direction</span><strong id="historicalReplayForecastStat">—</strong></article>
        <article><span>Revealed close error</span><strong id="historicalReplayErrorStat">—</strong></article>
      </div>
      <div class="historical-replay-chart-wrap">
        <div id="historicalReplayChart"></div>
        <div class="historical-replay-legend">
          <span><i class="historical-replay-dot history"></i>Known history</span>
          <span><i class="historical-replay-dot forecast"></i>Frozen Kronos</span>
          <span><i class="historical-replay-dot actual"></i>Revealed actual</span>
        </div>
      </div>
      <div class="historical-replay-note"><strong>Intrabar reconstruction:</strong> completed OHLC candles do not contain the order in which the high and low occurred. For replay only, bullish candles animate Open → Low → High → Close and bearish candles animate Open → High → Low → Close. The recorded O/H/L/C values themselves are unchanged.</div>
    </div>
  `;
}

function historicalReplayDestroyChart() {
  if (historicalReplayChart) {
    try { historicalReplayChart.remove(); } catch (_) {}
  }
  historicalReplayChart = null;
  historicalReplayHistorySeries = null;
  historicalReplayForecastSeries = null;
  historicalReplayForecastLine = null;
  historicalReplayActualSeries = null;
}

function historicalReplayBuildChart(payload) {
  historicalReplayDestroyChart();
  const node = document.getElementById('historicalReplayChart');
  if (!node) return;

  historicalReplayChart = LightweightCharts.createChart(node, {
    autoSize: true,
    layout: { background: { type: 'solid', color: '#070b1b' }, textColor: '#7f8aa8', attributionLogo: false },
    grid: { vertLines: { color: 'rgba(139,158,213,.045)' }, horzLines: { color: 'rgba(139,158,213,.045)' } },
    rightPriceScale: { borderColor: 'rgba(139,158,213,.13)', scaleMargins: { top: .08, bottom: .10 } },
    timeScale: { borderColor: 'rgba(139,158,213,.13)', timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 7 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const spec = typeof symbolPriceSpec === 'function'
    ? symbolPriceSpec(payload.symbol, null)
    : { precision: 4, minMove: .0001 };
  const priceFormat = { type: 'price', precision: spec.precision, minMove: spec.minMove };

  historicalReplayHistorySeries = historicalReplayChart.addSeries(LightweightCharts.CandlestickSeries, {
    priceFormat,
    upColor: '#2dd4bf', downColor: '#fb7185', borderVisible: false,
    wickUpColor: '#2dd4bf', wickDownColor: '#fb7185', priceLineVisible: false, lastValueVisible: false,
  });
  historicalReplayForecastSeries = historicalReplayChart.addSeries(LightweightCharts.CandlestickSeries, {
    priceFormat,
    upColor: 'rgba(96,165,250,.34)', downColor: 'rgba(139,92,246,.34)',
    borderUpColor: 'rgba(147,197,253,.52)', borderDownColor: 'rgba(167,139,250,.52)',
    wickUpColor: 'rgba(96,165,250,.44)', wickDownColor: 'rgba(139,92,246,.44)',
    priceLineVisible: false, lastValueVisible: false,
  });
  historicalReplayForecastLine = historicalReplayChart.addSeries(LightweightCharts.LineSeries, {
    priceFormat,
    color: '#8b5cf6', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: false, lastValueVisible: false,
  });
  historicalReplayActualSeries = historicalReplayChart.addSeries(LightweightCharts.CandlestickSeries, {
    priceFormat,
    upColor: '#2dd4bf', downColor: '#fb7185', borderVisible: false,
    wickUpColor: '#2dd4bf', wickDownColor: '#fb7185', priceLineVisible: false, lastValueVisible: false,
  });

  const history = historicalReplayRows(payload.history).slice(-HISTORICAL_REPLAY_HISTORY_VISIBLE);
  const forecast = historicalReplayRows(payload.projection);
  historicalReplayHistorySeries.setData(history.map(toCandle));
  historicalReplayForecastSeries.setData(forecast.map(toCandle));
  historicalReplayForecastLine.setData(forecast.map(toLine));
  historicalReplayActualSeries.setData([]);
  historicalReplayChart.timeScale().fitContent();
}

function historicalReplayUpdateStats() {
  const payload = historicalReplayPayload;
  if (!payload) return;
  const actual = historicalReplayRows(payload.actual);
  const forecast = historicalReplayRows(payload.projection);
  const history = historicalReplayRows(payload.history);
  const base = Number(history.at(-1)?.close);
  const revealedIndex = historicalReplayIndex - 1;

  document.getElementById('historicalReplayCutoffStat').textContent = new Date(payload.cutoff_timestamp).toLocaleString();
  document.getElementById('historicalReplayContextStat').textContent = `${payload.context_candles} candles`;
  document.getElementById('historicalReplayForecastStat').textContent = payload.summary?.forecast_direction
    ? String(payload.summary.forecast_direction).replace(/^./, value => value.toUpperCase())
    : historicalReplayDirection(base, Number(forecast.at(-1)?.close));

  if (revealedIndex >= 0 && actual[revealedIndex] && forecast[revealedIndex]) {
    const observed = Number(actual[revealedIndex].close);
    const predicted = Number(forecast[revealedIndex].close);
    const error = Math.abs(predicted - observed) / Math.max(Math.abs(observed), 1e-12) * 100;
    document.getElementById('historicalReplayErrorStat').textContent = `${error.toFixed(3)}%`;
  } else {
    document.getElementById('historicalReplayErrorStat').textContent = '—';
  }

  const progress = document.getElementById('historicalReplayProgress');
  if (progress) {
    const total = actual.length;
    const latest = historicalReplayIndex > 0 ? actual[historicalReplayIndex - 1] : null;
    progress.textContent = latest
      ? `${historicalReplayIndex}/${total} revealed · close ${historicalReplayFormatPrice(latest.close)}`
      : `0/${total} revealed · forecast frozen at ${new Date(payload.cutoff_timestamp).toLocaleString()}`;
  }

  const atEnd = historicalReplayIndex >= actual.length;
  const play = document.getElementById('historicalReplayPlay');
  const step = document.getElementById('historicalReplayStep');
  if (play) {
    play.disabled = atEnd || !payload;
    play.textContent = historicalReplayPlaying ? 'Ⅱ Pause' : '▶ Play';
  }
  if (step) step.disabled = atEnd || historicalReplayPlaying || !payload;
}

function historicalReplaySetActual(rows, transient = null) {
  if (!historicalReplayActualSeries) return;
  const data = rows.map(toCandle);
  if (transient) data.push(transient);
  historicalReplayActualSeries.setData(data);
}

function historicalReplaySleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function historicalReplayAnimateSegment(current, targetPrice, duration, token, completed) {
  const from = Number(current.close);
  const started = performance.now();
  while (true) {
    if (token !== historicalReplayToken) return false;
    const elapsed = performance.now() - started;
    const progress = Math.min(1, elapsed / Math.max(24, duration));
    const eased = progress * progress * (3 - 2 * progress);
    const price = from + (targetPrice - from) * eased;
    current.close = price;
    current.high = Math.max(current.high, price);
    current.low = Math.min(current.low, price);
    historicalReplaySetActual(completed, { ...current });
    if (progress >= 1) return true;
    await historicalReplaySleep(16);
  }
}

async function historicalReplayAnimateOne(index, token) {
  const payload = historicalReplayPayload;
  if (!payload || index >= payload.actual.length) return false;

  const actual = historicalReplayRows(payload.actual);
  const row = actual[index];
  const completed = actual.slice(0, index);
  const speed = Math.max(.25, Number(document.getElementById('historicalReplaySpeed')?.value || 1));
  const segmentMs = 145 / speed;
  const bullish = row.close >= row.open;
  const sequence = bullish ? [row.low, row.high, row.close] : [row.high, row.low, row.close];
  const current = {
    time: toTime(row.timestamp),
    open: row.open,
    high: row.open,
    low: row.open,
    close: row.open,
  };
  historicalReplaySetActual(completed, { ...current });

  for (const target of sequence) {
    const finished = await historicalReplayAnimateSegment(current, Number(target), segmentMs, token, completed);
    if (!finished) return false;
  }

  historicalReplayActualSeries.setData(actual.slice(0, index + 1).map(toCandle));
  historicalReplayIndex = index + 1;
  historicalReplayUpdateStats();
  return true;
}

function historicalReplayPause() {
  historicalReplayPlaying = false;
  historicalReplayToken += 1;
  historicalReplayUpdateStats();
}

async function historicalReplayPlay() {
  if (!historicalReplayPayload) return;
  if (historicalReplayPlaying) {
    historicalReplayPause();
    return;
  }
  historicalReplayPlaying = true;
  const token = ++historicalReplayToken;
  historicalReplayUpdateStats();

  while (historicalReplayPlaying && token === historicalReplayToken
         && historicalReplayIndex < historicalReplayPayload.actual.length) {
    const finished = await historicalReplayAnimateOne(historicalReplayIndex, token);
    if (!finished) break;
    await historicalReplaySleep(45);
  }

  if (token === historicalReplayToken) {
    historicalReplayPlaying = false;
    historicalReplayUpdateStats();
  }
}

async function historicalReplayStep() {
  if (!historicalReplayPayload || historicalReplayPlaying) return;
  const token = ++historicalReplayToken;
  await historicalReplayAnimateOne(historicalReplayIndex, token);
}

function historicalReplayReset() {
  historicalReplayPause();
  historicalReplayIndex = 0;
  if (historicalReplayActualSeries) historicalReplayActualSeries.setData([]);
  historicalReplayUpdateStats();
  if (historicalReplayChart) historicalReplayChart.timeScale().fitContent();
}

async function historicalReplayGenerate() {
  const generate = document.getElementById('historicalReplayGenerate');
  const cutoffInput = document.getElementById('historicalReplayCutoff');
  const horizonInput = document.getElementById('historicalReplayHorizon');
  if (!generate || !cutoffInput || !horizonInput) return;

  const horizon = Math.max(1, Math.min(200, Number(horizonInput.value) || HISTORICAL_REPLAY_DEFAULT_HORIZON));
  const cutoff = Math.max(horizon, Math.min(4500, Number(cutoffInput.value) || HISTORICAL_REPLAY_DEFAULT_CUTOFF));
  horizonInput.value = String(horizon);
  cutoffInput.value = String(cutoff);

  historicalReplayPause();
  historicalReplayPayload = null;
  historicalReplayIndex = 0;
  generate.disabled = true;
  generate.textContent = 'Running Kronos…';
  document.getElementById('historicalReplayProgress').textContent = `Freezing ${state.symbol} ${state.timeframe} at historical cutoff…`;
  ['historicalReplayPlay', 'historicalReplayStep', 'historicalReplayReset'].forEach(id => {
    const button = document.getElementById(id);
    if (button) button.disabled = true;
  });

  try {
    const payload = await api('/v1/replay/kronos', {
      method: 'POST',
      body: JSON.stringify({
        symbol: state.symbol,
        timeframe: state.timeframe,
        cutoff_ago: cutoff,
        pred_len: horizon,
      }),
    });
    historicalReplayPayload = payload;
    historicalReplayIndex = 0;
    historicalReplayBuildChart(payload);
    historicalReplayUpdateStats();
    document.getElementById('historicalReplayReset').disabled = false;
    const seconds = Number(payload.inference_ms || 0) / 1000;
    toast(`Historical Kronos forecast ready in ${seconds.toFixed(1)}s.`, 'success');
  } catch (error) {
    document.getElementById('historicalReplayProgress').textContent = error.message;
    toast(error.message, 'error');
  } finally {
    generate.disabled = false;
    generate.textContent = 'Generate Kronos forecast';
  }
}

function installHistoricalReplayUI() {
  const panel = document.querySelector('[data-content="replay"]');
  if (!panel || panel.dataset.kronosReplayInstalled === 'true') return;
  panel.dataset.kronosReplayInstalled = 'true';
  installHistoricalReplayStyles();
  panel.innerHTML = historicalReplayMarkup();

  const oldDisclosure = document.getElementById('replayModelDisclosure');
  if (oldDisclosure) oldDisclosure.remove();

  document.getElementById('historicalReplayGenerate')?.addEventListener('click', historicalReplayGenerate);
  document.getElementById('historicalReplayPlay')?.addEventListener('click', historicalReplayPlay);
  document.getElementById('historicalReplayStep')?.addEventListener('click', historicalReplayStep);
  document.getElementById('historicalReplayReset')?.addEventListener('click', historicalReplayReset);
  document.getElementById('historicalReplayHorizon')?.addEventListener('input', event => {
    const horizon = Math.max(1, Number(event.target.value) || 1);
    const cutoff = document.getElementById('historicalReplayCutoff');
    if (cutoff && Number(cutoff.value) < horizon) cutoff.value = String(horizon);
  });

  const replayTab = document.querySelector('[data-tab="replay"]');
  if (replayTab) {
    replayTab.textContent = 'Replay';
    replayTab.title = 'Historical single-cutoff Kronos replay';
  }
  const toolbarButton = document.getElementById('openReplayBacktest');
  if (toolbarButton) {
    toolbarButton.textContent = 'Replay';
    toolbarButton.title = 'Open historical Kronos replay';
  }
}

queueMicrotask(installHistoricalReplayUI);
