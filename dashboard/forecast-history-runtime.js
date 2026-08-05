const PRIOR_FORECAST_VISIBILITY_KEY = 'traidPriorForecastsVisible';
const LOCKED_FORECAST_STORAGE_KEY = 'traidLockedForecastsV1';
const LOCKED_FORECAST_TRANSITION_MS = 250;

let priorForecastsVisible = localStorage.getItem(PRIOR_FORECAST_VISIBILITY_KEY) !== 'false';
let lockedForecastRecord = null;
let lockedForecastStyleProgress = 0;
let lockedForecastStyleFrame = null;

const lockedCandles = chart.addSeries(LightweightCharts.CandlestickSeries, {
  upColor: 'rgba(148,163,184,.33)',
  downColor: 'rgba(148,163,184,.33)',
  borderUpColor: 'rgba(148,163,184,.33)',
  borderDownColor: 'rgba(148,163,184,.33)',
  wickUpColor: 'rgba(148,163,184,.33)',
  wickDownColor: 'rgba(148,163,184,.33)',
  priceLineVisible: false,
  lastValueVisible: false,
  visible: false,
});
const lockedLine = chart.addSeries(LightweightCharts.LineSeries, {
  color: 'rgba(148,163,184,.33)',
  lineWidth: 3,
  priceLineVisible: false,
  lastValueVisible: false,
  visible: false,
});
const lockedVolume = chart.addSeries(LightweightCharts.HistogramSeries, {
  priceScaleId: 'volume',
  priceFormat: { type: 'volume' },
  priceLineVisible: false,
  lastValueVisible: false,
  visible: false,
});

function forecastLockKey(symbol = state.symbol, timeframe = state.timeframe) {
  return `${String(symbol || '').toUpperCase()}|${String(timeframe || '')}`;
}

function readLockedForecasts() {
  try {
    const stored = JSON.parse(localStorage.getItem(LOCKED_FORECAST_STORAGE_KEY) || '{}');
    return stored && typeof stored === 'object' ? stored : {};
  } catch (_) {
    return {};
  }
}

function writeLockedForecasts(records) {
  try {
    localStorage.setItem(LOCKED_FORECAST_STORAGE_KEY, JSON.stringify(records));
  } catch (error) {
    console.warn('Could not persist locked forecast.', error);
  }
}

function forecastHistoryRows(index) {
  const rows = state.projectionHistory[index] || [];
  return Array.isArray(rows) ? cloneProjection(rows) : [];
}

function rgba(values) {
  return `rgba(${Math.round(values[0])},${Math.round(values[1])},${Math.round(values[2])},${values[3].toFixed(3)})`;
}

function mixLockedColor(from, to, progress) {
  return from.map((value, index) => value + (to[index] - value) * progress);
}

function applyLockedForecastStyle(progress) {
  const grayscale = [148, 163, 184, 0.33];
  const activeUp = [96, 165, 250, 0.96];
  const activeDown = [139, 92, 246, 0.96];
  const activeBorderUp = [147, 197, 253, 1];
  const activeBorderDown = [167, 139, 250, 1];
  const activeLine = [139, 92, 246, 1];
  const volumeGray = [148, 163, 184, 0.11];
  const volumeColor = [139, 92, 246, 0.32];

  lockedCandles.applyOptions({
    upColor: rgba(mixLockedColor(grayscale, activeUp, progress)),
    downColor: rgba(mixLockedColor(grayscale, activeDown, progress)),
    borderUpColor: rgba(mixLockedColor(grayscale, activeBorderUp, progress)),
    borderDownColor: rgba(mixLockedColor(grayscale, activeBorderDown, progress)),
    wickUpColor: rgba(mixLockedColor(grayscale, activeUp, progress)),
    wickDownColor: rgba(mixLockedColor(grayscale, activeDown, progress)),
  });
  lockedLine.applyOptions({
    color: rgba(mixLockedColor(grayscale, activeLine, progress)),
  });
  lockedVolume.applyOptions({
    color: rgba(mixLockedColor(volumeGray, volumeColor, progress)),
  });
}

function animateLockedForecastStyle(target) {
  const clampedTarget = Math.max(0, Math.min(1, Number(target) || 0));
  if (lockedForecastStyleFrame) cancelAnimationFrame(lockedForecastStyleFrame);
  const start = lockedForecastStyleProgress;
  if (Math.abs(start - clampedTarget) < 0.001) {
    lockedForecastStyleProgress = clampedTarget;
    applyLockedForecastStyle(clampedTarget);
    return;
  }

  const startedAt = performance.now();
  const frame = now => {
    const linear = Math.min(1, (now - startedAt) / LOCKED_FORECAST_TRANSITION_MS);
    const eased = linear * linear * (3 - 2 * linear);
    lockedForecastStyleProgress = start + (clampedTarget - start) * eased;
    applyLockedForecastStyle(lockedForecastStyleProgress);
    if (linear < 1) lockedForecastStyleFrame = requestAnimationFrame(frame);
    else lockedForecastStyleFrame = null;
  };
  lockedForecastStyleFrame = requestAnimationFrame(frame);
}

function applyLockedForecastPriceFormat() {
  if (typeof symbolPriceSpec !== 'function') return;
  const spec = symbolPriceSpec(state.symbol, state.quote);
  const options = {
    priceFormat: {
      type: 'price',
      precision: spec.precision,
      minMove: spec.minMove,
    },
  };
  lockedCandles.applyOptions(options);
  lockedLine.applyOptions(options);
}

function applyLockedPredictionVisibility() {
  const visible = Boolean(lockedForecastRecord?.rows?.length);
  const candlesVisible = visible && state.chartType === 'candles';
  const lineVisible = visible && state.chartType === 'line';
  lockedCandles.applyOptions({ visible: candlesVisible });
  lockedLine.applyOptions({ visible: lineVisible });
  lockedVolume.applyOptions({ visible });
  if (!visible) animateLockedForecastStyle(0);
}

function renderLockedForecast() {
  const rows = Array.isArray(lockedForecastRecord?.rows)
    ? cloneProjection(lockedForecastRecord.rows)
    : [];
  applyLockedForecastPriceFormat();
  lockedCandles.setData(rows.map(toCandle));
  lockedLine.setData(rows.map(toLine));
  lockedVolume.setData(rows.map(row => toVolume(row, 'rgba(148,163,184,.11)')));
  lockedForecastStyleProgress = 0;
  applyLockedForecastStyle(0);
  applyLockedPredictionVisibility();
  refreshForecastHistoryControls();
}

function restoreLockedForecast(symbol = state.symbol, timeframe = state.timeframe) {
  const records = readLockedForecasts();
  const candidate = records[forecastLockKey(symbol, timeframe)] || null;
  const validRows = Array.isArray(candidate?.rows) && candidate.rows.length
    && (typeof rowsMatchTimeframe !== 'function' || rowsMatchTimeframe(candidate.rows, timeframe));
  lockedForecastRecord = validRows ? candidate : null;
  renderLockedForecast();
}

function lockForecastGeneration(index, label) {
  const rows = forecastHistoryRows(index);
  if (!rows.length) return;
  const records = readLockedForecasts();
  const key = forecastLockKey();
  const record = {
    symbol: state.symbol,
    timeframe: state.timeframe,
    source: label,
    locked_at: new Date().toISOString(),
    rows,
  };
  records[key] = record;
  writeLockedForecasts(records);
  lockedForecastRecord = record;
  renderLockedForecast();
  if (typeof fitCurrentMarket === 'function') fitCurrentMarket({ fitTime: false });
}

function unlockForecast() {
  const records = readLockedForecasts();
  delete records[forecastLockKey()];
  writeLockedForecasts(records);
  lockedForecastRecord = null;
  renderLockedForecast();
  if (typeof fitCurrentMarket === 'function') fitCurrentMarket({ fitTime: false });
}

function updatePriorLegendState() {
  document.querySelectorAll('.chart-status-row .legend span').forEach(node => {
    const prior = node.textContent.includes('Previous') || node.textContent.includes('Older');
    if (!prior) return;
    node.style.opacity = priorForecastsVisible ? '' : '0.34';
    node.style.textDecoration = priorForecastsVisible ? '' : 'line-through';
  });
}

function applyPriorPredictionVisibility() {
  const candles = priorForecastsVisible && state.chartType === 'candles';
  const lines = priorForecastsVisible && state.chartType === 'line';
  previousCandles.applyOptions({ visible: candles });
  olderCandles.applyOptions({ visible: candles });
  previousLine.applyOptions({ visible: lines });
  olderLine.applyOptions({ visible: lines });
  previousVolume.applyOptions({ visible: priorForecastsVisible });
  olderVolume.applyOptions({ visible: priorForecastsVisible });

  const toggle = document.getElementById('priorPredictionsToggle');
  if (toggle) toggle.checked = priorForecastsVisible;
  updatePriorLegendState();
}

function togglePriorPredictions(visible) {
  priorForecastsVisible = Boolean(visible);
  localStorage.setItem(PRIOR_FORECAST_VISIBILITY_KEY, String(priorForecastsVisible));
  applyPriorPredictionVisibility();
}

function closeForecastLockMenu() {
  const menu = document.getElementById('forecastLockMenu');
  const button = document.getElementById('forecastLockButton');
  if (menu) menu.classList.remove('open');
  if (button) button.setAttribute('aria-expanded', 'false');
}

function refreshForecastHistoryControls() {
  const button = document.getElementById('forecastLockButton');
  if (button) {
    const locked = Boolean(lockedForecastRecord?.rows?.length);
    button.classList.toggle('active', locked);
    button.textContent = locked ? 'Locked' : 'Lock';
    button.setAttribute('aria-pressed', String(locked));
    button.title = locked
      ? `${lockedForecastRecord.source || 'Forecast'} locked for ${state.symbol} ${state.timeframe}`
      : 'Lock a forecast on the chart';
  }

  const availability = [
    ['active', 0],
    ['previous', 1],
    ['older', 2],
  ];
  for (const [name, index] of availability) {
    const option = document.querySelector(`[data-lock-forecast="${name}"]`);
    if (option) option.disabled = !forecastHistoryRows(index).length;
  }
  const unlock = document.querySelector('[data-lock-forecast="unlock"]');
  if (unlock) unlock.disabled = !lockedForecastRecord;
  applyPriorPredictionVisibility();
  applyLockedPredictionVisibility();
}

function installForecastHistoryControls() {
  if (document.getElementById('forecastHistoryControls')) return;
  const toolbar = document.querySelector('.chart-toolbar');
  if (!toolbar) return;

  const style = document.createElement('style');
  style.id = 'forecastHistoryControlStyles';
  style.textContent = `
    .forecast-history-controls {
      position:relative; display:flex; align-items:center; gap:8px; margin-left:auto;
    }
    .forecast-prior-switch { white-space:nowrap; }
    .forecast-lock-wrap { position:relative; }
    .forecast-lock-button {
      min-height:34px; padding:0 12px; border:1px solid rgba(148,163,184,.18);
      border-radius:10px; background:rgba(15,23,42,.48); color:#cbd5e1;
      font:750 11px/1 system-ui,sans-serif; cursor:pointer;
    }
    .forecast-lock-button:hover, .forecast-lock-button.active {
      border-color:rgba(139,92,246,.46); color:#fff; background:rgba(76,29,149,.28);
    }
    .forecast-lock-menu {
      position:absolute; right:0; top:calc(100% + 7px); z-index:40; display:none;
      min-width:142px; padding:5px; border:1px solid rgba(148,163,184,.18);
      border-radius:10px; background:rgba(7,11,27,.97); box-shadow:0 12px 30px rgba(0,0,0,.36);
    }
    .forecast-lock-menu.open { display:grid; gap:3px; }
    .forecast-lock-menu button {
      width:100%; padding:8px 9px; border:0; border-radius:7px; background:transparent;
      color:#cbd5e1; text-align:left; font:650 11px/1.2 system-ui,sans-serif; cursor:pointer;
    }
    .forecast-lock-menu button:hover:not(:disabled) { background:rgba(99,102,241,.18); color:#fff; }
    .forecast-lock-menu button:disabled { opacity:.35; cursor:not-allowed; }
    .forecast-lock-menu button.unlock { color:#fda4af; border-top:1px solid rgba(148,163,184,.12); margin-top:2px; }
    @media (max-width:900px) {
      .forecast-history-controls { margin-left:0; gap:6px; }
      .forecast-lock-button { min-height:32px; padding:0 10px; }
    }
  `;
  document.head.appendChild(style);

  const group = document.createElement('div');
  group.id = 'forecastHistoryControls';
  group.className = 'forecast-history-controls';
  group.innerHTML = `
    <label class="switch-control forecast-prior-switch" title="Show previous and older forecasts">
      <input id="priorPredictionsToggle" type="checkbox" />
      <span class="switch"></span><span>Previous</span>
    </label>
    <div class="forecast-lock-wrap">
      <button id="forecastLockButton" class="forecast-lock-button" type="button" aria-expanded="false" aria-pressed="false">Lock</button>
      <div id="forecastLockMenu" class="forecast-lock-menu" role="menu">
        <button type="button" data-lock-forecast="active">Lock active</button>
        <button type="button" data-lock-forecast="previous">Lock previous</button>
        <button type="button" data-lock-forecast="older">Lock older</button>
        <button type="button" class="unlock" data-lock-forecast="unlock">Unlock</button>
      </div>
    </div>
  `;

  const advanced = document.getElementById('advancedForecast')?.closest('label');
  toolbar.insertBefore(group, advanced || document.getElementById('refreshForecast') || null);

  document.getElementById('priorPredictionsToggle')?.addEventListener('change', event => {
    togglePriorPredictions(event.target.checked);
  });
  document.getElementById('forecastLockButton')?.addEventListener('click', event => {
    event.stopPropagation();
    const menu = document.getElementById('forecastLockMenu');
    const opening = !menu?.classList.contains('open');
    if (menu) menu.classList.toggle('open', opening);
    event.currentTarget.setAttribute('aria-expanded', String(opening));
  });
  document.getElementById('forecastLockMenu')?.addEventListener('click', event => {
    const option = event.target.closest('[data-lock-forecast]');
    if (!option || option.disabled) return;
    const action = option.dataset.lockForecast;
    if (action === 'active') lockForecastGeneration(0, 'Active forecast');
    else if (action === 'previous') lockForecastGeneration(1, 'Previous forecast');
    else if (action === 'older') lockForecastGeneration(2, 'Older forecast');
    else if (action === 'unlock') unlockForecast();
    closeForecastLockMenu();
  });
  document.addEventListener('click', closeForecastLockMenu);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeForecastLockMenu();
  });

  refreshForecastHistoryControls();
}

function lockedForecastHoverState(param) {
  if (!lockedForecastRecord || !param?.point || param.point.y == null) return false;
  const series = state.chartType === 'candles' ? lockedCandles : lockedLine;
  const datum = param.seriesData?.get(series);
  if (!datum) return false;

  if (state.chartType === 'candles') {
    const coordinates = [datum.open, datum.high, datum.low, datum.close]
      .map(price => lockedCandles.priceToCoordinate(Number(price)))
      .filter(Number.isFinite);
    if (!coordinates.length) return false;
    const top = Math.min(...coordinates) - 11;
    const bottom = Math.max(...coordinates) + 11;
    return param.point.y >= top && param.point.y <= bottom;
  }

  const coordinate = lockedLine.priceToCoordinate(Number(datum.value));
  return Number.isFinite(coordinate) && Math.abs(param.point.y - coordinate) <= 13;
}

chart.subscribeCrosshairMove(param => {
  animateLockedForecastStyle(lockedForecastHoverState(param) ? 1 : 0);
});

installForecastHistoryControls();
restoreLockedForecast(state.symbol, state.timeframe);
applyPriorPredictionVisibility();
