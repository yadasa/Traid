const PRIOR_FORECAST_VISIBILITY_KEY = 'traidPriorForecastsVisible';
const LOCKED_FORECAST_STORAGE_KEY = 'traidLockedForecastsV2';
const LEGACY_LOCKED_FORECAST_STORAGE_KEY = 'traidLockedForecastsV1';
const MAX_LOCKED_FORECASTS = 20;
const LOCKED_FORECAST_TRANSITION_MS = 250;

let priorForecastsVisible = localStorage.getItem(PRIOR_FORECAST_VISIBILITY_KEY) !== 'false';
let lockedForecastRecords = [];
let lockedForecastVisuals = [];

// Compatibility state retained for the runtime-loader safety patch. Individual
// locked forecasts use their own guarded animation state below.
let lockedForecastStyleProgress = 0;
let lockedForecastStyleFrame = null;

function forecastLockKey(symbol = state.symbol, timeframe = state.timeframe) {
  return `${String(symbol || '').toUpperCase()}|${String(timeframe || '')}`;
}

function makeLockedForecastId(record) {
  const rows = Array.isArray(record?.rows) ? record.rows : [];
  const first = rows[0] || {};
  const last = rows.at(-1) || {};
  return [
    String(record?.symbol || '').toUpperCase(),
    String(record?.timeframe || ''),
    String(first.timestamp || ''),
    String(last.timestamp || ''),
    String(last.close ?? ''),
  ].join('|');
}

function normalizeLockedForecastRecord(record) {
  if (!record || !Array.isArray(record.rows) || !record.rows.length) return null;
  const symbol = String(record.symbol || '').toUpperCase();
  const timeframe = String(record.timeframe || '');
  if (!symbol || !timeframe) return null;
  const normalized = {
    ...record,
    symbol,
    timeframe,
    locked_at: record.locked_at || new Date().toISOString(),
    rows: cloneProjection(record.rows),
  };
  normalized.id = record.id || makeLockedForecastId(normalized);
  return normalized;
}

function normalizeLockedForecastCollection(records) {
  const byId = new Map();
  for (const candidate of Array.isArray(records) ? records : []) {
    const record = normalizeLockedForecastRecord(candidate);
    if (record) byId.set(record.id, record);
  }
  return [...byId.values()]
    .sort((first, second) => new Date(first.locked_at).getTime() - new Date(second.locked_at).getTime())
    .slice(-MAX_LOCKED_FORECASTS);
}

function readLockedForecasts() {
  try {
    const current = JSON.parse(localStorage.getItem(LOCKED_FORECAST_STORAGE_KEY) || 'null');
    if (Array.isArray(current)) return normalizeLockedForecastCollection(current);

    const legacy = JSON.parse(localStorage.getItem(LEGACY_LOCKED_FORECAST_STORAGE_KEY) || '{}');
    const migrated = legacy && typeof legacy === 'object'
      ? normalizeLockedForecastCollection(Object.values(legacy))
      : [];
    if (migrated.length) writeLockedForecasts(migrated);
    return migrated;
  } catch (_) {
    return [];
  }
}

function writeLockedForecasts(records) {
  const normalized = normalizeLockedForecastCollection(records);
  try {
    localStorage.setItem(LOCKED_FORECAST_STORAGE_KEY, JSON.stringify(normalized));
    localStorage.removeItem(LEGACY_LOCKED_FORECAST_STORAGE_KEY);
  } catch (error) {
    console.warn('Could not persist locked forecasts.', error);
  }
  return normalized;
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

function lockedForecastPriceOptions() {
  if (typeof symbolPriceSpec !== 'function') return {};
  const spec = symbolPriceSpec(state.symbol, state.quote);
  return {
    priceFormat: {
      type: 'price',
      precision: spec.precision,
      minMove: spec.minMove,
    },
  };
}

function applyLockedVisualStyle(visual, progress) {
  const grayscale = [148, 163, 184, 0.33];
  const activeUp = [96, 165, 250, 0.96];
  const activeDown = [139, 92, 246, 0.96];
  const activeBorderUp = [147, 197, 253, 1];
  const activeBorderDown = [167, 139, 250, 1];
  const activeLine = [139, 92, 246, 1];

  visual.candles.applyOptions({
    upColor: rgba(mixLockedColor(grayscale, activeUp, progress)),
    downColor: rgba(mixLockedColor(grayscale, activeDown, progress)),
    borderUpColor: rgba(mixLockedColor(grayscale, activeBorderUp, progress)),
    borderDownColor: rgba(mixLockedColor(grayscale, activeBorderDown, progress)),
    wickUpColor: rgba(mixLockedColor(grayscale, activeUp, progress)),
    wickDownColor: rgba(mixLockedColor(grayscale, activeDown, progress)),
  });
  visual.line.applyOptions({
    color: rgba(mixLockedColor(grayscale, activeLine, progress)),
  });
}

function animateLockedVisual(visual, target) {
  const clampedTarget = Math.max(0, Math.min(1, Number(target) || 0));
  if (clampedTarget === visual.target
      && (visual.frame || Math.abs(visual.progress - clampedTarget) < 0.001)) {
    return;
  }
  visual.target = clampedTarget;
  if (visual.frame) cancelAnimationFrame(visual.frame);
  const start = visual.progress;
  if (Math.abs(start - clampedTarget) < 0.001) {
    visual.progress = clampedTarget;
    applyLockedVisualStyle(visual, clampedTarget);
    return;
  }

  const startedAt = performance.now();
  const frame = now => {
    const linear = Math.min(1, (now - startedAt) / LOCKED_FORECAST_TRANSITION_MS);
    const eased = linear * linear * (3 - 2 * linear);
    visual.progress = start + (clampedTarget - start) * eased;
    applyLockedVisualStyle(visual, visual.progress);
    if (linear < 1) visual.frame = requestAnimationFrame(frame);
    else visual.frame = null;
  };
  visual.frame = requestAnimationFrame(frame);
}

// Compatibility wrapper retained for the loader's recursion guard.
function animateLockedForecastStyle(target) {
  const clampedTarget = Math.max(0, Math.min(1, Number(target) || 0));
  lockedForecastStyleProgress = clampedTarget;
  lockedForecastStyleFrame = null;
}

function removeLockedForecastVisuals() {
  for (const visual of lockedForecastVisuals) {
    if (visual.frame) cancelAnimationFrame(visual.frame);
    try { chart.removeSeries(visual.candles); } catch (_) {}
    try { chart.removeSeries(visual.line); } catch (_) {}
  }
  lockedForecastVisuals = [];
}

function createLockedForecastVisual(record) {
  const priceOptions = lockedForecastPriceOptions();
  const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
    ...priceOptions,
    upColor: 'rgba(148,163,184,.33)',
    downColor: 'rgba(148,163,184,.33)',
    borderUpColor: 'rgba(148,163,184,.33)',
    borderDownColor: 'rgba(148,163,184,.33)',
    wickUpColor: 'rgba(148,163,184,.33)',
    wickDownColor: 'rgba(148,163,184,.33)',
    priceLineVisible: false,
    lastValueVisible: false,
    visible: state.chartType === 'candles',
  });
  const line = chart.addSeries(LightweightCharts.LineSeries, {
    ...priceOptions,
    color: 'rgba(148,163,184,.33)',
    lineWidth: 3,
    priceLineVisible: false,
    lastValueVisible: false,
    visible: state.chartType === 'line',
  });
  const rows = cloneProjection(record.rows);
  candles.setData(rows.map(toCandle));
  line.setData(rows.map(toLine));
  const visual = {
    record,
    candles,
    line,
    progress: 0,
    target: 0,
    frame: null,
  };
  applyLockedVisualStyle(visual, 0);
  return visual;
}

function currentChartLockedForecasts() {
  const key = forecastLockKey();
  return lockedForecastRecords.filter(record => forecastLockKey(record.symbol, record.timeframe) === key);
}

function renderLockedForecasts() {
  removeLockedForecastVisuals();
  const records = currentChartLockedForecasts();
  lockedForecastVisuals = records.map(createLockedForecastVisual);
  applyLockedPredictionVisibility();
  refreshForecastHistoryControls();
}

function renderLockedForecast() {
  renderLockedForecasts();
}

function restoreLockedForecast(symbol = state.symbol, timeframe = state.timeframe) {
  lockedForecastRecords = readLockedForecasts();
  const key = forecastLockKey(symbol, timeframe);
  lockedForecastRecords = lockedForecastRecords.filter(record => {
    if (forecastLockKey(record.symbol, record.timeframe) !== key) return true;
    return typeof rowsMatchTimeframe !== 'function' || rowsMatchTimeframe(record.rows, timeframe);
  });
  lockedForecastRecords = writeLockedForecasts(lockedForecastRecords);
  renderLockedForecasts();
}

function lockForecastGeneration(index, label) {
  const rows = forecastHistoryRows(index);
  if (!rows.length) return;

  const now = new Date().toISOString();
  const record = normalizeLockedForecastRecord({
    symbol: state.symbol,
    timeframe: state.timeframe,
    source: label,
    locked_at: now,
    rows,
  });
  if (!record) return;

  const records = readLockedForecasts().filter(existing => existing.id !== record.id);
  records.push(record);
  lockedForecastRecords = writeLockedForecasts(records);
  renderLockedForecasts();
  if (typeof fitCurrentMarket === 'function') fitCurrentMarket({ fitTime: false });
}

function unlockForecast() {
  const key = forecastLockKey();
  const relevant = readLockedForecasts()
    .filter(record => forecastLockKey(record.symbol, record.timeframe) === key)
    .sort((first, second) => new Date(first.locked_at).getTime() - new Date(second.locked_at).getTime());
  const latest = relevant.at(-1);
  if (!latest) return;
  lockedForecastRecords = writeLockedForecasts(
    readLockedForecasts().filter(record => record.id !== latest.id),
  );
  renderLockedForecasts();
  if (typeof fitCurrentMarket === 'function') fitCurrentMarket({ fitTime: false });
}

function clearLockedForecastsForCurrentChart() {
  const key = forecastLockKey();
  lockedForecastRecords = writeLockedForecasts(
    readLockedForecasts().filter(record => forecastLockKey(record.symbol, record.timeframe) !== key),
  );
  renderLockedForecasts();
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

function applyLockedPredictionVisibility() {
  const candlesVisible = state.chartType === 'candles';
  const lineVisible = state.chartType === 'line';
  for (const visual of lockedForecastVisuals) {
    visual.candles.applyOptions({ visible: candlesVisible });
    visual.line.applyOptions({ visible: lineVisible });
  }
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
  const currentCount = currentChartLockedForecasts().length;
  const totalCount = lockedForecastRecords.length;
  const button = document.getElementById('forecastLockButton');
  if (button) {
    button.classList.toggle('active', currentCount > 0);
    button.textContent = currentCount ? `Locked ${currentCount}` : 'Lock';
    button.setAttribute('aria-pressed', String(currentCount > 0));
    button.title = `${currentCount} locked on this chart · ${totalCount}/${MAX_LOCKED_FORECASTS} total`;
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
  if (unlock) unlock.disabled = currentCount === 0;
  const clear = document.querySelector('[data-lock-forecast="clear"]');
  if (clear) clear.disabled = currentCount === 0;
  const counter = document.getElementById('forecastLockCounter');
  if (counter) counter.textContent = `${totalCount}/${MAX_LOCKED_FORECASTS} locked`;
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
      position:absolute; right:0; top:calc(100% + 7px); z-index:100; display:none;
      min-width:156px; padding:5px; border:1px solid rgba(148,163,184,.18);
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
    .forecast-lock-counter { padding:5px 9px 4px; color:#7f8aa8; font:650 9px/1 system-ui,sans-serif; }
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
        <div id="forecastLockCounter" class="forecast-lock-counter">0/20 locked</div>
        <button type="button" data-lock-forecast="active">Lock active</button>
        <button type="button" data-lock-forecast="previous">Lock previous</button>
        <button type="button" data-lock-forecast="older">Lock older</button>
        <button type="button" class="unlock" data-lock-forecast="unlock">Unlock latest</button>
        <button type="button" class="unlock" data-lock-forecast="clear">Clear chart locks</button>
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
    else if (action === 'clear') clearLockedForecastsForCurrentChart();
    closeForecastLockMenu();
  });
  document.addEventListener('click', closeForecastLockMenu);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeForecastLockMenu();
  });

  refreshForecastHistoryControls();
}

function lockedVisualHoverState(visual, param) {
  if (!param?.point || param.point.y == null) return false;
  const series = state.chartType === 'candles' ? visual.candles : visual.line;
  const datum = param.seriesData?.get(series);
  if (!datum) return false;

  if (state.chartType === 'candles') {
    const coordinates = [datum.open, datum.high, datum.low, datum.close]
      .map(price => visual.candles.priceToCoordinate(Number(price)))
      .filter(Number.isFinite);
    if (!coordinates.length) return false;
    const top = Math.min(...coordinates) - 11;
    const bottom = Math.max(...coordinates) + 11;
    return param.point.y >= top && param.point.y <= bottom;
  }

  const coordinate = visual.line.priceToCoordinate(Number(datum.value));
  return Number.isFinite(coordinate) && Math.abs(param.point.y - coordinate) <= 13;
}

chart.subscribeCrosshairMove(param => {
  let hovered = null;
  for (const visual of lockedForecastVisuals) {
    if (lockedVisualHoverState(visual, param)) {
      hovered = visual;
      break;
    }
  }
  for (const visual of lockedForecastVisuals) {
    animateLockedVisual(visual, visual === hovered ? 1 : 0);
  }
});

installForecastHistoryControls();
restoreLockedForecast(state.symbol, state.timeframe);
applyPriorPredictionVisibility();
