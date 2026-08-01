const $ = id => document.getElementById(id);
const FORECAST_TRANSITION_MS = 333;
const names = { XAUUSD: 'Gold / U.S. Dollar', XAGUSD: 'Silver / U.S. Dollar', NAS100: 'Nasdaq 100 CFD', SPX500: 'S&P 500 CFD' };
const state = {
  symbol: localStorage.getItem('traidSymbol') || 'XAUUSD',
  timeframe: localStorage.getItem('traidTimeframe') || '5m',
  chartType: localStorage.getItem('traidChartType') || 'candles',
  side: 'buy', orderKind: 'market', socket: null, quote: null,
  firstClose: null, lastQuoteAt: 0, priceLine: null,
  projectionHistory: [], renderedProjection: [], projectionAnimationFrame: null,
  currentForecastId: null, token: sessionStorage.getItem('traidToken') || '',
  principal: JSON.parse(sessionStorage.getItem('traidPrincipal') || 'null'),
  tradingStatus: null, positions: [], pending: [], settings: {}, eventMarkers: null,
};

const apiBase = () => ($('apiUrl')?.value || localStorage.getItem('traidApiUrl') || 'http://localhost:8000').trim().replace(/\/$/, '');
const authHeaders = (json = true) => ({ ...(json ? { 'Content-Type': 'application/json' } : {}), ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) });
const toTime = value => Math.floor(new Date(value).getTime() / 1000);
const toCandle = row => ({ time: toTime(row.timestamp), open: +row.open, high: +row.high, low: +row.low, close: +row.close });
const toLine = row => ({ time: toTime(row.timestamp), value: +row.close });
const toVolume = (row, color) => ({ time: toTime(row.timestamp), value: Math.max(0, +(row.volume || 0)), color });
const formatNumber = (value, digits = 2) => value == null || Number.isNaN(+value) ? '—' : Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const formatMoney = (value, currency = 'USD') => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(value);
const formatPct = value => value == null ? '—' : `${value >= 0 ? '+' : ''}${formatNumber(value, 2)}%`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, options = {}) {
  const response = await fetch(`${apiBase()}${path}`, { ...options, headers: { ...authHeaders(options.body != null), ...(options.headers || {}) } });
  let payload;
  try { payload = await response.json(); } catch { payload = { detail: await response.text() }; }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function toast(message, type = '') {
  const node = document.createElement('div');
  node.className = `toast ${type}`; node.textContent = message;
  $('toastStack').appendChild(node); setTimeout(() => node.remove(), 4500);
}

function setFeed(connected, detail = '') {
  $('connectionDot').style.background = connected ? 'var(--green)' : 'var(--red)';
  $('feedPill').textContent = connected ? '● LIVE' : '● OFFLINE';
  $('feedPill').className = connected ? 'pill live' : 'pill locked';
  if (detail) $('providerText').textContent = detail;
}

const chart = LightweightCharts.createChart($('chart'), {
  autoSize: true,
  layout: { background: { type: 'solid', color: '#070b1b' }, textColor: '#7f8aa8', attributionLogo: false },
  grid: { vertLines: { color: 'rgba(139,158,213,.052)' }, horzLines: { color: 'rgba(139,158,213,.052)' } },
  rightPriceScale: { borderColor: 'rgba(139,158,213,.13)', scaleMargins: { top: .08, bottom: .22 } },
  timeScale: { borderColor: 'rgba(139,158,213,.13)', timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 8 },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: 'rgba(96,165,250,.28)' }, horzLine: { color: 'rgba(96,165,250,.28)' } },
  handleScroll: true, handleScale: true,
});

const marketCandles = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: '#2dd4bf', downColor: '#fb7185', borderVisible: false, wickUpColor: '#2dd4bf', wickDownColor: '#fb7185', priceLineVisible: false });
const marketLine = chart.addSeries(LightweightCharts.LineSeries, { color: '#2dd4bf', lineWidth: 2, priceLineVisible: false, visible: false });
const liveCandles = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: '#22d3ee', downColor: '#38bdf8', borderVisible: false, wickUpColor: '#67e8f9', wickDownColor: '#7dd3fc', priceLineVisible: false, lastValueVisible: false });
const liveLine = chart.addSeries(LightweightCharts.LineSeries, { color: '#22d3ee', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, visible: false });
const olderCandles = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: 'rgba(143,151,162,.33)', downColor: 'rgba(152,139,168,.33)', borderUpColor: 'rgba(151,158,168,.33)', borderDownColor: 'rgba(160,148,175,.33)', wickUpColor: 'rgba(143,151,162,.33)', wickDownColor: 'rgba(152,139,168,.33)', priceLineVisible: false, lastValueVisible: false });
const previousCandles = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: 'rgba(119,157,205,.67)', downColor: 'rgba(147,118,202,.67)', borderUpColor: 'rgba(132,169,214,.67)', borderDownColor: 'rgba(160,132,214,.67)', wickUpColor: 'rgba(119,157,205,.67)', wickDownColor: 'rgba(147,118,202,.67)', priceLineVisible: false, lastValueVisible: false });
const forecastCandles = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: 'rgba(96,165,250,.94)', downColor: 'rgba(139,92,246,.94)', borderUpColor: '#93c5fd', borderDownColor: '#a78bfa', wickUpColor: '#60a5fa', wickDownColor: '#8b5cf6', priceLineVisible: false, lastValueVisible: false });
const olderLine = chart.addSeries(LightweightCharts.LineSeries, { color: 'rgba(148,145,166,.33)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: false });
const previousLine = chart.addSeries(LightweightCharts.LineSeries, { color: 'rgba(139,131,207,.67)', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, visible: false });
const forecastLine = chart.addSeries(LightweightCharts.LineSeries, { color: '#8b5cf6', lineWidth: 3, priceLineVisible: false, lastValueVisible: false, visible: false });
const p10Line = chart.addSeries(LightweightCharts.LineSeries, { color: 'rgba(96,165,250,.28)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
const p90Line = chart.addSeries(LightweightCharts.LineSeries, { color: 'rgba(167,139,250,.32)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
const marketVolume = chart.addSeries(LightweightCharts.HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
const olderVolume = chart.addSeries(LightweightCharts.HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
const previousVolume = chart.addSeries(LightweightCharts.HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
const forecastVolume = chart.addSeries(LightweightCharts.HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
chart.priceScale('volume').applyOptions({ scaleMargins: { top: .84, bottom: 0 }, borderVisible: false });

let replayChart;
let replaySeries;

function applyChartType() {
  const candles = state.chartType === 'candles';
  [marketCandles, liveCandles, olderCandles, previousCandles, forecastCandles].forEach(series => series.applyOptions({ visible: candles }));
  [marketLine, liveLine, olderLine, previousLine, forecastLine].forEach(series => series.applyOptions({ visible: !candles }));
  document.querySelectorAll('[data-chart-type]').forEach(button => button.classList.toggle('active', button.dataset.chartType === state.chartType));
  localStorage.setItem('traidChartType', state.chartType);
}

function setHistory(rows) {
  marketCandles.setData(rows.map(toCandle)); marketLine.setData(rows.map(toLine));
  marketVolume.setData(rows.map(row => toVolume(row, +row.close >= +row.open ? 'rgba(45,212,191,.23)' : 'rgba(251,113,133,.21)')));
  state.firstClose = rows.length ? +rows[0].close : null;
}

function setCurrent(row) {
  if (!row) { liveCandles.setData([]); liveLine.setData([]); return; }
  liveCandles.setData([toCandle(row)]); liveLine.setData([toLine(row)]);
  marketVolume.update(toVolume(row, +row.close >= +row.open ? 'rgba(34,211,238,.24)' : 'rgba(56,189,248,.22)'));
}

function updateQuote(quote) {
  if (!quote) return;
  state.quote = quote; state.lastQuoteAt = Date.now();
  const digits = +quote.price > 1000 ? 2 : 4;
  $('livePrice').textContent = formatNumber(quote.price, digits);
  document.querySelector(`[data-watch-price="${state.symbol}"]`).textContent = formatNumber(quote.price, digits);
  if (state.firstClose) {
    const change = +quote.price - state.firstClose; const pct = change / state.firstClose * 100;
    $('priceChange').textContent = `${change >= 0 ? '+' : ''}${formatNumber(change, digits)} (${formatPct(pct)})`;
    $('priceChange').className = `price-change ${change >= 0 ? 'up' : 'down'}`;
  }
  if (!state.priceLine) state.priceLine = marketCandles.createPriceLine({ price: +quote.price, color: '#22d3ee', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'LIVE' });
  else state.priceLine.applyOptions({ price: +quote.price });
  $('providerText').textContent = `${quote.source || 'market feed'}${quote.delayed ? ' · delayed' : ' · real time'}`;
}

const cloneProjection = rows => rows.map(row => ({ ...row, open: +row.open, high: +row.high, low: +row.low, close: +row.close, volume: Math.max(0, +(row.volume || 0)), amount: Math.max(0, +(row.amount || 0)) }));
const ease = progress => progress * progress * (3 - 2 * progress);

function resetProjectionHistory() {
  if (state.projectionAnimationFrame) cancelAnimationFrame(state.projectionAnimationFrame);
  state.projectionHistory = []; state.renderedProjection = []; state.projectionAnimationFrame = null;
  [olderCandles, previousCandles, forecastCandles, olderLine, previousLine, forecastLine, p10Line, p90Line].forEach(series => series.setData([]));
  [olderVolume, previousVolume, forecastVolume].forEach(series => series.setData([]));
}

function renderPriorForecasts() {
  const previous = state.projectionHistory[1] || []; const older = state.projectionHistory[2] || [];
  previousCandles.setData(previous.map(toCandle)); previousLine.setData(previous.map(toLine));
  olderCandles.setData(older.map(toCandle)); olderLine.setData(older.map(toLine));
  previousVolume.setData(previous.map(row => toVolume(row, 'rgba(132,128,165,.21)')));
  olderVolume.setData(older.map(row => toVolume(row, 'rgba(145,145,155,.105)')));
}

function alignForecast(fromRows, toRows) {
  if (!fromRows.length) return cloneProjection(toRows);
  const byTime = new Map(fromRows.map(row => [toTime(row.timestamp), row]));
  return toRows.map((target, index) => ({ ...(byTime.get(toTime(target.timestamp)) || fromRows[Math.min(index, fromRows.length - 1)] || target), timestamp: target.timestamp }));
}

function interpolateForecast(fromRows, toRows, progress) {
  return toRows.map((target, index) => {
    const start = fromRows[index] || target;
    const mix = key => +start[key] + (+target[key] - +start[key]) * progress;
    return { ...target, open: mix('open'), high: mix('high'), low: mix('low'), close: mix('close'), volume: mix('volume'), amount: mix('amount') };
  });
}

function drawActiveForecast(rows) {
  forecastCandles.setData(rows.map(toCandle)); forecastLine.setData(rows.map(toLine));
  forecastVolume.setData(rows.map(row => toVolume(row, 'rgba(139,92,246,.32)'));
}

function animateActiveForecast(fromRows, toRows) {
  if (state.projectionAnimationFrame) cancelAnimationFrame(state.projectionAnimationFrame);
  const target = cloneProjection(toRows);
  if (!fromRows.length || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    drawActiveForecast(target); state.renderedProjection = target; return;
  }
  const startRows = alignForecast(fromRows, target); const started = performance.now();
  const frame = now => {
    const linear = Math.min(1, (now - started) / FORECAST_TRANSITION_MS);
    const current = interpolateForecast(startRows, target, ease(linear));
    drawActiveForecast(current); state.renderedProjection = current;
    if (linear < 1) state.projectionAnimationFrame = requestAnimationFrame(frame);
    else { state.renderedProjection = target; state.projectionAnimationFrame = null; }
  };
  state.projectionAnimationFrame = requestAnimationFrame(frame);
}

function setUncertainty(uncertainty) {
  if (!state.settings.advanced && !$('advancedForecast').checked || !uncertainty) {
    p10Line.setData([]); p90Line.setData([]); return;
  }
  p10Line.setData((uncertainty.p10 || []).map(toLine));
  p90Line.setData((uncertainty.p90 || []).map(toLine));
}

function setProjection(rows, uncertainty = null, revision = null, generatedAt = null) {
  const next = cloneProjection(rows); const priorActive = state.projectionHistory[0] || [];
  const animationStart = state.renderedProjection.length ? cloneProjection(state.renderedProjection) : cloneProjection(priorActive);
  const priorPrevious = state.projectionHistory[1] || [];
  state.projectionHistory = [next, priorActive, priorPrevious].filter(item => item.length).slice(0, 3);
  renderPriorForecasts(); animateActiveForecast(animationStart, next); setUncertainty(uncertainty);
  $('forecastStatus').textContent = generatedAt ? `Updated ${new Date(generatedAt).toLocaleTimeString()}` : `Active ${rows.length} candles`;
  $('forecastPill').textContent = `FORECAST ${rows.length}`;
  renderRevision(revision);
}

async function primeProjectionHistory(currentId) {
  try {
    const payload = await api(`/v1/forecasts/${state.symbol}?timeframe=${state.timeframe}&limit=3`);
    const history = payload.forecasts.filter(item => item.id !== currentId).slice(0, 2).map(item => item.projection);
    state.projectionHistory = [state.projectionHistory[0] || [], ...history].filter(item => item.length).slice(0, 3);
    renderPriorForecasts(); renderAccuracy(payload.accuracy);
  } catch (error) { console.warn(error); }
}

function renderRevision(revision) {
  const visible = $('advancedForecast').checked;
  $('advancedStrip').classList.toggle('hidden', !visible);
  if (!visible || !revision?.available) {
    ['revisionSeverity','pathSimilarity','magnitudeChange','volatilityChange'].forEach(id => $(id).textContent = '—');
    $('revisionDirection').textContent = 'Waiting for prior forecast'; return;
  }
  $('revisionSeverity').textContent = revision.severity.toUpperCase();
  $('revisionDirection').textContent = `${revision.direction_previous} → ${revision.direction_active}${revision.direction_flip ? ' · FLIP' : ''}`;
  $('pathSimilarity').textContent = `${formatNumber(revision.path_similarity_pct, 1)}%`;
  $('magnitudeChange').textContent = formatPct(revision.magnitude_change_pct_points);
  $('timingShift').textContent = `Timing shift ${revision.timing_shift_candles >= 0 ? '+' : ''}${revision.timing_shift_candles} candles`;
  $('volatilityChange').textContent = formatPct(revision.volatility_change_pct);
  $('candleConsensus').textContent = `Candle consensus ${formatNumber(revision.candle_consensus_pct, 0)}%`;
  if (revision.direction_flip && ['moderate','major'].includes(revision.severity)) notify('Traid forecast revision', `${state.symbol} ${state.timeframe}: ${revision.direction_previous} → ${revision.direction_active} (${revision.severity})`);
}

function renderAccuracy(accuracy = {}) {
  const direction = accuracy.direction_accuracy; const range = accuracy.range_hit_rate;
  $('accuracyDirection').textContent = direction == null ? '—' : `${formatNumber(direction, 1)}%`;
  $('accuracyError').textContent = accuracy.mean_close_error_pct == null ? '—' : `${formatNumber(accuracy.mean_close_error_pct, 3)}%`;
  $('accuracyRange').textContent = range == null ? '—' : `${formatNumber(range, 1)}%`;
  $('accuracySamples').textContent = accuracy.samples || 0;
  $('sidebarAccuracy').textContent = direction == null ? '—' : `${formatNumber(direction, 0)}%`;
  $('sidebarRange').textContent = range == null ? '—' : `${formatNumber(range, 0)}%`;
  $('sidebarSamples').textContent = accuracy.samples || 0;
  $('horizonAccuracy').innerHTML = Object.entries(accuracy.by_horizon || {}).slice(0, 12).map(([horizon, item]) => `<div class="list-row"><span>Candle ${horizon}</span><strong>${formatNumber(item.direction_accuracy, 0)}% direction · ${formatNumber(item.mean_close_error_pct, 3)}% error</strong></div>`).join('') || '<div class="list-row">No realized forecast scores yet.</div>';
}

function renderContext(payload = {}) {
  const multi = payload.multi_timeframe || {};
  $('timeframeConsensus').textContent = (multi.consensus || '—').toUpperCase();
  $('timeframeAgreement').textContent = `Agreement ${formatNumber(multi.agreement_pct, 0)}%`;
  const markets = payload.cross_market?.markets || [];
  $('crossMarketContext').innerHTML = markets.map(item => `<div class="list-row"><span>${item.symbol}</span><strong class="${item.move_pct >= 0 ? 'positive' : 'negative'}">${item.direction} · ${formatPct(item.move_pct)}</strong></div>`).join('') || '<div class="list-row">Generate forecasts across markets to build context.</div>';
}

function wsBase() { const url = new URL(apiBase()); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'; return url.toString().replace(/\/$/, ''); }

async function loadMarket({ fit = true } = {}) {
  if (state.socket) state.socket.close();
  resetProjectionHistory(); setFeed(false, 'Loading market and model…');
  $('marketName').textContent = names[state.symbol]; $('symbolSelect').value = state.symbol;
  $('watermarkMarket').textContent = `${state.symbol} · ${state.timeframe}`;
  $('forecastStatus').textContent = $('advancedForecast').checked ? 'Generating advanced forecast…' : 'Generating forecast…';
  document.querySelectorAll('.watch-item').forEach(item => item.classList.toggle('active', item.dataset.symbol === state.symbol));
  try {
    const payload = await api('/v1/forecast', { method: 'POST', body: JSON.stringify({ symbol: state.symbol, timeframe: state.timeframe, lookback: 400, pred_len: Math.max(1, +$('predLen').value || 24), sample_count: 5, advanced: $('advancedForecast').checked, uncertainty_paths: $('advancedForecast').checked ? Math.max(3, +$('uncertaintyPaths').value || 7) : null }) });
    state.currentForecastId = payload.id; setHistory(payload.history); setCurrent(payload.current_candle);
    state.projectionHistory = [cloneProjection(payload.projection)]; state.renderedProjection = [];
    setProjection(payload.projection, payload.uncertainty, payload.revision, payload.generated_at);
    updateQuote(payload.quote); renderAccuracy(payload.accuracy); await primeProjectionHistory(payload.id);
    await loadContext(); if (fit) chart.timeScale().fitContent(); connectStream(); setFeed(true);
  } catch (error) { setFeed(false, error.message); toast(error.message, 'error'); }
}

function connectStream() {
  const url = `${wsBase()}/v1/stream/${state.symbol}?timeframe=${state.timeframe}&with_forecast=true&advanced=${$('advancedForecast').checked}&pred_len=${Math.max(1,+$('predLen').value||24)}`;
  const socket = new WebSocket(url); state.socket = socket;
  socket.onopen = () => setFeed(true);
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === 'market_update') {
      updateQuote(message.quote); setCurrent(message.current_candle);
      if (message.completed_candle) { marketCandles.update(toCandle(message.completed_candle)); marketLine.update(toLine(message.completed_candle)); marketVolume.update(toVolume(message.completed_candle, +message.completed_candle.close >= +message.completed_candle.open ? 'rgba(45,212,191,.23)' : 'rgba(251,113,133,.21)')); liveCandles.setData([]); liveLine.setData([]); }
      if (message.forecast_status) $('forecastStatus').textContent = message.forecast_status === 'queued' ? 'Forecast refresh queued…' : 'Refreshing forecast…';
    } else if (message.type === 'projection_update') {
      state.currentForecastId = message.id; setProjection(message.projection, message.uncertainty, message.revision, message.generated_at); renderAccuracy(message.accuracy); loadContext();
    } else if (message.type === 'forecast_status') $('forecastStatus').textContent = 'Refreshing queued forecast…';
    else if (message.type === 'forecast_error' || message.type === 'error') toast(message.detail, 'error');
  };
  socket.onclose = () => setFeed(false, 'Live stream disconnected');
  socket.onerror = () => setFeed(false, 'Live stream error');
}

async function loadContext() { try { renderContext(await api(`/v1/forecast-context/${state.symbol}?timeframe=${state.timeframe}`)); } catch {} }

async function refreshWatchlist() {
  await Promise.all(Object.keys(names).map(async symbol => {
    try { const payload = await api(`/v1/quote/${symbol}?timeframe=${state.timeframe}`); const node = document.querySelector(`[data-watch-price="${symbol}"]`); if (node) node.textContent = formatNumber(payload.quote.price, payload.quote.price > 1000 ? 2 : 4); } catch {}
  }));
}

async function loadCalendar() {
  try {
    const now = new Date(); const end = new Date(Date.now() + 7 * 86400000);
    const payload = await api(`/v1/calendar?start=${encodeURIComponent(now.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);
    const events = payload.events || [];
    $('calendarList').innerHTML = events.map(event => `<article class="timeline-item"><time>${new Date(event.starts_at).toLocaleString([], { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' })}</time><div><strong>${escapeHtml(event.title)}</strong><small>${event.currency} · ${escapeHtml(event.source || 'Manual')}</small></div><span class="impact ${event.impact}">${event.impact}</span></article>`).join('') || '<div class="empty-cell">No upcoming events. Configure TRAID_CALENDAR_URL or add one manually.</div>';
    const next = events[0]; $('nextEvent').innerHTML = next ? `<strong>${escapeHtml(next.title)}</strong><small>${new Date(next.starts_at).toLocaleString()} · ${next.impact.toUpperCase()}</small>` : '<strong>No event loaded</strong><small>Add a calendar source or event.</small>';
  } catch (error) { console.warn(error); }
}

async function loadTrading() {
  updateRoleUI();
  if (!state.token) { state.tradingStatus = null; setTradingLocked('Authenticate to trade'); return; }
  try {
    const status = await api('/v1/trading/status'); state.tradingStatus = status;
    const account = status.account || {}; const currency = account.currency || 'USD';
    $('accountEquity').textContent = formatMoney(account.equity, currency); $('accountBalance').textContent = formatMoney(account.balance, currency); $('freeMargin').textContent = formatMoney(account.margin_free, currency); $('accountServer').textContent = account.server || 'MT5';
    $('tradingPill').textContent = status.enabled ? `${status.mode.toUpperCase()} TRADING` : 'TRADING DISABLED'; $('tradingPill').className = status.mode === 'live' ? 'pill locked' : 'pill paper';
    const risk = status.risk || {}; $('riskBanner').className = `risk-banner ${risk.allowed ? 'safe' : 'blocked'}`; $('riskBanner').innerHTML = `<span class="risk-dot"></span><div><strong>${risk.allowed ? 'Risk controls permit a trade' : 'Trading blocked'}</strong><small>${risk.reasons?.join(' ') || `Daily P/L ${formatMoney(risk.daily_pnl || 0, currency)} · Open risk ${formatMoney(risk.open_risk || 0, currency)}`}</small></div>`;
    $('placeOrderButton').disabled = !status.enabled || !risk.allowed || state.principal?.role !== 'admin'; $('placeOrderButton').textContent = !status.enabled ? 'Trading disabled by server' : state.principal?.role !== 'admin' ? 'Admin access required' : `${state.side === 'buy' ? 'Buy' : 'Sell'} ${state.symbol}`;
    await loadPositions(); await updateRiskPreview();
  } catch (error) { setTradingLocked(error.message); }
}

function setTradingLocked(message) {
  $('tradingPill').textContent = 'TRADING LOCKED'; $('tradingPill').className = 'pill locked';
  $('placeOrderButton').disabled = true; $('placeOrderButton').textContent = message;
  $('riskBanner').className = 'risk-banner'; $('riskBanner').innerHTML = `<span class="risk-dot"></span><div><strong>Risk engine unavailable</strong><small>${escapeHtml(message)}</small></div>`;
}

async function loadPositions() {
  try {
    const payload = await api('/v1/trading/positions'); state.positions = payload.positions || []; state.pending = payload.pending_orders || [];
    $('positionSummary').textContent = `${state.positions.length} open position${state.positions.length === 1 ? '' : 's'} · ${state.pending.length} pending`;
    $('positionsBody').innerHTML = state.positions.map(position => `<tr><td>${position.symbol}</td><td><span class="side-badge ${position.side}">${position.side}</span></td><td>${position.volume}</td><td>${formatNumber(position.open_price,4)}</td><td>${formatNumber(position.current_price,4)}</td><td>${position.stop_loss ? formatNumber(position.stop_loss,4) : '—'}</td><td>${position.take_profit ? formatNumber(position.take_profit,4) : '—'}</td><td class="${position.profit >= 0 ? 'positive' : 'negative'}">${formatNumber(position.profit,2)}</td><td>${position.trailing ? `Fixed ${formatNumber(position.trailing.distance,2)}` : 'Off'}</td><td><button class="text-button" data-break-even="${position.ticket}">BE</button> <button class="text-button" data-close-position="${position.ticket}">Close</button></td></tr>`).join('') || '<tr><td colspan="10" class="empty-cell">No Traid-managed positions.</td></tr>';
    $('pendingBody').innerHTML = state.pending.map(order => `<tr><td>${order.ticket}</td><td>${order.symbol}</td><td>${order.volume_initial}</td><td>${formatNumber(order.price_open,4)}</td><td>${formatNumber(order.stop_loss,4)}</td><td>${formatNumber(order.take_profit,4)}</td><td><button class="text-button" data-cancel-order="${order.ticket}">Cancel</button></td></tr>`).join('') || '<tr><td colspan="7" class="empty-cell">No pending orders.</td></tr>';
    document.querySelectorAll('[data-close-position]').forEach(button => button.onclick = () => closePosition(+button.dataset.closePosition));
    document.querySelectorAll('[data-break-even]').forEach(button => button.onclick = () => breakEven(+button.dataset.breakEven));
    document.querySelectorAll('[data-cancel-order]').forEach(button => button.onclick = () => cancelPending(+button.dataset.cancelOrder));
  } catch (error) { console.warn(error); }
}

async function updateRiskPreview() {
  const stop = +$('stopDistance').value; const take = +$('takeProfit').value;
  $('riskReward').textContent = stop > 0 && take > 0 ? `1:${formatNumber(take / stop,2)}` : '—';
  if (!state.token || $('sizeMode').value !== 'risk' || !stop) { $('calculatedLots').textContent = $('sizeMode').value === 'lots' ? `${$('volume').value} lots` : '—'; $('estimatedLoss').textContent = '—'; return; }
  try {
    const result = await api('/v1/trading/risk-size', { method:'POST', body: JSON.stringify({ symbol:state.symbol, stop_distance:stop, risk_percent:+$('riskPercent').value }) });
    $('calculatedLots').textContent = `${result.volume} lots`; $('estimatedLoss').textContent = formatMoney(result.estimated_loss, state.tradingStatus?.account?.currency || 'USD');
  } catch { $('calculatedLots').textContent = '—'; $('estimatedLoss').textContent = '—'; }
}

async function submitOrder(event) {
  event.preventDefault();
  if (!state.token) return openSettings();
  const button = $('placeOrderButton'); button.disabled = true;
  try {
    const stopDistance = +$('stopDistance').value; const takeDistance = +$('takeProfit').value || null;
    if (state.orderKind === 'market') {
      const body = { symbol:state.symbol, side:state.side, volume:$('sizeMode').value === 'lots' ? +$('volume').value : null, risk_percent:$('sizeMode').value === 'risk' ? +$('riskPercent').value : null, stop_loss_distance:stopDistance, take_profit_distance:takeDistance, trailing_distance:$('trailingKind').value === 'fixed' ? (+$('trailDistance').value || null) : null, trailing_step:+$('trailStep').value || 0, trailing_activation:+$('trailActivation').value || 0, client_order_id:crypto.randomUUID(), forecast_id:state.currentForecastId, entry_reason:$('entryReason').value || null, confirm_live:$('confirmLive').checked };
      const result = await api('/v1/trading/orders', { method:'POST', body:JSON.stringify(body) });
      toast(result.paper ? 'Paper market order passed MT5 preflight.' : `Order executed at ${result.fill_price}`, 'success');
      if (result.position_ticket && $('trailingKind').value !== 'fixed' && +$('trailDistance').value > 0) await api(`/v1/trading/positions/${result.position_ticket}/smart-trailing`, { method:'PUT', body:JSON.stringify({ kind:$('trailingKind').value, value:+$('trailDistance').value, activation:+$('trailActivation').value || 0, step:+$('trailStep').value || 0, timeframe:state.timeframe, lookback:14 }) });
    } else {
      const entry = +$('entryPrice').value; if (!entry) throw new Error('Enter a pending-order price.');
      const isBuy = state.side === 'buy'; const kind = `${state.side}_${state.orderKind}`;
      const body = { symbol:state.symbol, kind, volume:$('sizeMode').value === 'lots' ? +$('volume').value : parseFloat($('calculatedLots').textContent) || 0.01, price:entry, stop_loss:isBuy ? entry - stopDistance : entry + stopDistance, take_profit:takeDistance ? (isBuy ? entry + takeDistance : entry - takeDistance) : null, client_order_id:crypto.randomUUID(), confirm_live:$('confirmLive').checked };
      const result = await api('/v1/trading/pending', { method:'POST', body:JSON.stringify(body) });
      toast(result.paper ? 'Paper pending order passed MT5 preflight.' : `Pending order ${result.order_ticket} placed.`, 'success');
    }
    await loadTrading();
  } catch (error) { toast(error.message, 'error'); }
  finally { button.disabled = !(state.tradingStatus?.enabled && state.tradingStatus?.risk?.allowed && state.principal?.role === 'admin'); }
}

async function closePosition(ticket) { if (!confirm(`Close position ${ticket}?`)) return; try { const result = await api(`/v1/trading/positions/${ticket}/close`, { method:'POST', body:JSON.stringify({ confirm_live:$('confirmLive').checked, exit_reason:'Manual dashboard close' }) }); toast(result.executed ? 'Position closed.' : 'Paper close passed preflight.', 'success'); loadTrading(); } catch(error) { toast(error.message,'error'); } }
async function breakEven(ticket) { try { await api(`/v1/trading/positions/${ticket}/break-even`, { method:'POST', body:JSON.stringify({ offset:0, confirm_live:$('confirmLive').checked }) }); toast('Stop moved to break-even.','success'); loadTrading(); } catch(error){ toast(error.message,'error'); } }
async function cancelPending(ticket) { if (!confirm(`Cancel pending order ${ticket}?`)) return; try { await api(`/v1/trading/pending/${ticket}?confirm_live=${$('confirmLive').checked}`, { method:'DELETE' }); toast('Pending order cancelled.','success'); loadTrading(); } catch(error){ toast(error.message,'error'); } }

async function loadJournal() {
  if (!state.token) { $('journalList').innerHTML = '<div class="empty-cell">Sign in to view the trading journal.</div>'; return; }
  try { const payload = await api('/v1/journal?limit=100'); $('journalList').innerHTML = payload.entries.map(entry => `<article class="journal-card"><header><h4>${entry.symbol} · ${entry.side.toUpperCase()}</h4><span class="pill ${entry.status === 'closed' ? '' : 'paper'}">${entry.status}</span></header><p>${escapeHtml(entry.entry_reason || entry.notes || 'No trade thesis recorded.')}</p><footer><span>${new Date(entry.created_at).toLocaleString()}</span><strong class="${(entry.pnl || 0) >= 0 ? 'positive' : 'negative'}">${entry.pnl == null ? 'Open' : formatNumber(entry.pnl,2)}</strong></footer></article>`).join('') || '<div class="empty-cell">No journal entries.</div>'; } catch(error){ $('journalList').innerHTML = `<div class="empty-cell">${escapeHtml(error.message)}</div>`; }
}

async function loadAudit() { if (!state.token) return; try { const payload = await api('/v1/audit?limit=200'); $('auditList').innerHTML = payload.entries.map(entry => `<div class="audit-item"><strong>${entry.action}</strong> · ${escapeHtml(entry.actor)} · ${new Date(entry.created_at).toLocaleString()}<br>${escapeHtml(JSON.stringify(entry.payload || {}))}</div>`).join(''); } catch(error){ toast(error.message,'error'); } }

async function runReplay() {
  $('runReplay').disabled = true;
  try {
    const result = await api('/v1/replay', { method:'POST', body:JSON.stringify({ symbol:state.symbol, timeframe:state.timeframe, start_index:+$('replayStart').value, steps:+$('replaySteps').value, pred_len:+$('replayHorizon').value }) });
    $('replayReturn').textContent = formatPct(result.return_pct); $('replayWinRate').textContent = `${formatNumber(result.win_rate_pct,1)}%`; $('replayDrawdown').textContent = `${formatNumber(result.max_drawdown_pct,2)}%`; $('replayTrades').textContent = result.steps;
    if (!replayChart) { replayChart = LightweightCharts.createChart($('replayChart'), { autoSize:true, layout:{background:{type:'solid',color:'transparent'},textColor:'#7f8aa8',attributionLogo:false}, grid:{vertLines:{color:'rgba(139,158,213,.05)'},horzLines:{color:'rgba(139,158,213,.05)'}}, rightPriceScale:{borderVisible:false}, timeScale:{borderVisible:false,timeVisible:true} }); replaySeries = replayChart.addSeries(LightweightCharts.AreaSeries,{lineColor:'#60a5fa',topColor:'rgba(59,130,246,.25)',bottomColor:'rgba(59,130,246,0)',priceLineVisible:false}); }
    replaySeries.setData(result.records.map(row => ({ time:toTime(row.timestamp), value:row.equity }))); replayChart.timeScale().fitContent();
  } catch(error){ toast(error.message,'error'); } finally { $('runReplay').disabled = false; }
}

async function login() { try { const result = await api('/v1/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})}); state.token=result.token; state.principal=result.principal; sessionStorage.setItem('traidToken',state.token); sessionStorage.setItem('traidPrincipal',JSON.stringify(state.principal)); $('password').value=''; updateRoleUI(); await loadTrading(); toast('Signed in.','success'); } catch(error){ toast(error.message,'error'); } }
async function logout() { try { await api('/v1/auth/logout',{method:'POST'}); } catch{} state.token=''; state.principal=null; sessionStorage.removeItem('traidToken'); sessionStorage.removeItem('traidPrincipal'); updateRoleUI(); setTradingLocked('Authenticate to trade'); }
function updateRoleUI() { const logged=!!state.principal; $('loginCard').classList.toggle('hidden',logged); $('sessionCard').classList.toggle('hidden',!logged); if(logged) $('principalName').textContent=`${state.principal.name} · ${state.principal.role}`; document.querySelectorAll('.admin-only').forEach(node=>node.classList.toggle('hidden',state.principal?.role!=='admin')); document.querySelectorAll('.trader-only').forEach(node=>node.classList.toggle('hidden',!['trader','admin'].includes(state.principal?.role))); }

function openSettings(){ $('settingsDrawer').classList.add('open'); $('settingsDrawer').setAttribute('aria-hidden','false'); $('modalBackdrop').classList.remove('hidden'); }
function closeSettings(){ $('settingsDrawer').classList.remove('open'); $('settingsDrawer').setAttribute('aria-hidden','true'); if(!$('tradePanel').classList.contains('open')&&!$('watchlistPanel').classList.contains('open')) $('modalBackdrop').classList.add('hidden'); }
function openTrade(){ $('tradePanel').classList.add('open'); $('modalBackdrop').classList.remove('hidden'); }
function closeTrade(){ $('tradePanel').classList.remove('open'); if(!$('settingsDrawer').classList.contains('open')&&!$('watchlistPanel').classList.contains('open')) $('modalBackdrop').classList.add('hidden'); }
function openWatchlist(){ $('watchlistPanel').classList.add('open'); $('modalBackdrop').classList.remove('hidden'); }
function closeWatchlist(){ $('watchlistPanel').classList.remove('open'); if(!$('settingsDrawer').classList.contains('open')&&!$('tradePanel').classList.contains('open')) $('modalBackdrop').classList.add('hidden'); }

function setTab(tab) { document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===tab)); document.querySelectorAll('[data-content]').forEach(content=>content.classList.toggle('active',content.dataset.content===tab)); if(tab==='calendar')loadCalendar(); if(tab==='journal')loadJournal(); if(tab==='audit')loadAudit(); if(tab==='positions')loadPositions(); }
function setSide(side){ state.side=side; document.querySelectorAll('[data-side]').forEach(button=>button.classList.toggle('active',button.dataset.side===side)); $('placeOrderButton').className=`primary-button ${side} full`; if(state.tradingStatus?.enabled)$('placeOrderButton').textContent=`${side==='buy'?'Buy':'Sell'} ${state.symbol}`; }
function setOrderKind(kind){ state.orderKind=kind; document.querySelectorAll('[data-order-kind]').forEach(button=>button.classList.toggle('active',button.dataset.orderKind===kind)); document.querySelectorAll('.pending-only').forEach(node=>node.classList.toggle('hidden',kind==='market')); }
function escapeHtml(value=''){ return String(value).replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char])); }

async function notify(title, body){ if(!$('notificationsEnabled').checked||Notification.permission!=='granted')return; new Notification(title,{body,tag:`traid-${state.symbol}-${state.timeframe}`}); }
async function enableNotifications(){ if(!('Notification'in window))return toast('Browser notifications are not supported.','error'); const permission=await Notification.requestPermission(); toast(permission==='granted'?'Notifications enabled.':'Notifications were not enabled.',permission==='granted'?'success':''); }

function wireUI(){
  $('symbolSelect').value=state.symbol; $('apiUrl').value=localStorage.getItem('traidApiUrl')||'http://localhost:8000'; $('predLen').value=localStorage.getItem('traidPredLen')||'24'; $('uncertaintyPaths').value=localStorage.getItem('traidUncertaintyPaths')||'7'; $('advancedForecast').checked=localStorage.getItem('traidAdvanced')==='true'; $('notificationsEnabled').checked=localStorage.getItem('traidNotifications')!=='false';
  document.querySelectorAll('[data-timeframe]').forEach(button=>{button.classList.toggle('active',button.dataset.timeframe===state.timeframe);button.onclick=()=>{state.timeframe=button.dataset.timeframe;localStorage.setItem('traidTimeframe',state.timeframe);document.querySelectorAll('[data-timeframe]').forEach(item=>item.classList.toggle('active',item===button));loadMarket();};});
  document.querySelectorAll('[data-chart-type]').forEach(button=>button.onclick=()=>{state.chartType=button.dataset.chartType;applyChartType();});
  document.querySelectorAll('.watch-item').forEach(button=>button.onclick=()=>{state.symbol=button.dataset.symbol;localStorage.setItem('traidSymbol',state.symbol);closeWatchlist();loadMarket();loadTrading();});
  $('symbolSelect').onchange=()=>{state.symbol=$('symbolSelect').value;localStorage.setItem('traidSymbol',state.symbol);loadMarket();loadTrading();};
  $('advancedForecast').onchange=()=>{localStorage.setItem('traidAdvanced',$('advancedForecast').checked);state.settings.advanced=$('advancedForecast').checked;$('advancedStrip').classList.toggle('hidden',!$('advancedForecast').checked);loadMarket();};
  $('refreshForecast').onclick=()=>loadMarket({fit:false});
  $('settingsButton').onclick=openSettings;$('settingsClose').onclick=closeSettings;$('modalBackdrop').onclick=()=>{closeSettings();closeTrade();closeWatchlist();};
  $('watchlistToggle').onclick=openWatchlist;$('watchlistClose').onclick=closeWatchlist;$('tradeClose').onclick=closeTrade;
  $('loginButton').onclick=login;$('logoutButton').onclick=logout;$('notificationButton').onclick=enableNotifications;
  $('saveSettings').onclick=()=>{localStorage.setItem('traidApiUrl',$('apiUrl').value);localStorage.setItem('traidPredLen',$('predLen').value);localStorage.setItem('traidUncertaintyPaths',$('uncertaintyPaths').value);localStorage.setItem('traidNotifications',$('notificationsEnabled').checked);closeSettings();loadMarket();loadTrading();};
  document.querySelectorAll('[data-side]').forEach(button=>button.onclick=()=>setSide(button.dataset.side));document.querySelectorAll('[data-order-kind]').forEach(button=>button.onclick=()=>setOrderKind(button.dataset.orderKind));
  $('sizeMode').onchange=()=>{$('riskPercentField').classList.toggle('hidden',$('sizeMode').value!=='risk');$('lotsField').classList.toggle('hidden',$('sizeMode').value!=='lots');updateRiskPreview();};
  ['riskPercent','volume','stopDistance','takeProfit'].forEach(id=>$(id).addEventListener('input',()=>{clearTimeout(window.riskTimer);window.riskTimer=setTimeout(updateRiskPreview,250);}));
  $('orderForm').onsubmit=submitOrder;$('refreshPositions').onclick=loadTrading;$('closeAllButton').onclick=async()=>{if(!confirm('Close every Traid-managed position?'))return;try{await api(`/v1/trading/positions/close-all?confirm_live=${$('confirmLive').checked}`,{method:'POST'});toast('Close-all request completed.','success');loadTrading();}catch(error){toast(error.message,'error');}};
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>setTab(button.dataset.tab));document.querySelectorAll('[data-open-tab]').forEach(button=>button.onclick=()=>{document.querySelector('.workspace').classList.add('show-analysis');setTab(button.dataset.openTab);});
  $('runReplay').onclick=runReplay;$('refreshAudit').onclick=loadAudit;
  $('addEventButton').onclick=async()=>{const title=prompt('Event title');if(!title)return;const startsAt=prompt('Start time (ISO or date/time)',new Date(Date.now()+3600000).toISOString());if(!startsAt)return;const impact=prompt('Impact: low, medium, or high','high')||'high';try{await api('/v1/calendar',{method:'POST',body:JSON.stringify({title,starts_at:new Date(startsAt).toISOString(),currency:'USD',impact})});loadCalendar();}catch(error){toast(error.message,'error');}};
  $('newJournalButton').onclick=async()=>{const reason=prompt('Trade thesis / note');if(!reason)return;try{await api('/v1/journal',{method:'POST',body:JSON.stringify({symbol:state.symbol,side:state.side,status:'planned',entry_reason:reason,tags:['manual'],metadata:{forecast_id:state.currentForecastId}})});loadJournal();}catch(error){toast(error.message,'error');}};
  document.querySelectorAll('[data-mobile-view]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-mobile-view]').forEach(item=>item.classList.toggle('active',item===button));const view=button.dataset.mobileView;if(view==='chart'){document.querySelector('.workspace').classList.remove('show-analysis');closeTrade();}else if(view==='analysis'){document.querySelector('.workspace').classList.add('show-analysis');setTab('forecast');closeTrade();}else if(view==='positions'){document.querySelector('.workspace').classList.add('show-analysis');setTab('positions');closeTrade();}else if(view==='trade'){openTrade();}else openSettings();});
  window.addEventListener('keydown',event=>{if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;const timeframes=['1m','5m','15m','30m','1h','4h','1d'];if(/^[1-7]$/.test(event.key)){state.timeframe=timeframes[+event.key-1];localStorage.setItem('traidTimeframe',state.timeframe);document.querySelector(`[data-timeframe="${state.timeframe}"]`)?.click();}if(event.key.toLowerCase()==='a')$('advancedForecast').click();if(event.key.toLowerCase()==='t')openTrade();if(event.key==='Escape'){closeSettings();closeTrade();closeWatchlist();}});
}

async function bootstrap(){
  wireUI();applyChartType();setSide('buy');setOrderKind('market');updateRoleUI();
  setInterval(()=>{$('clock').textContent=new Date().toISOString().slice(11,19);const stale=state.lastQuoteAt&&Date.now()-state.lastQuoteAt>15000;$('staleOverlay').classList.toggle('hidden',!stale);},1000);
  await Promise.allSettled([loadMarket(),loadCalendar(),loadTrading(),refreshWatchlist()]);
  setInterval(refreshWatchlist,15000);setInterval(()=>{if(state.token)loadTrading();},5000);
}

bootstrap();
