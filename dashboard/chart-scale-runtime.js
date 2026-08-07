const SYMBOL_PRICE_FORMATS = Object.freeze({
  XAUUSD: { precision: 2, minMove: 0.01 },
  XAGUSD: { precision: 4, minMove: 0.0001 },
  EURUSD: { precision: 5, minMove: 0.00001 },
  USDJPY: { precision: 3, minMove: 0.001 },
  NAS100: { precision: 2, minMove: 0.01 },
  SPX500: { precision: 2, minMove: 0.01 },
});

// Match completed-market colors exactly. The separate DOM halo supplies the
// oscillating glow without changing the candle's actual green/red hue.
const LIVE_DIRECTION_COLORS = Object.freeze({
  bullish: {
    body: '#2dd4bf',
    wick: '#2dd4bf',
    line: '#2dd4bf',
    volume: 'rgba(45,212,191,.24)',
  },
  bearish: {
    body: '#fb7185',
    wick: '#fb7185',
    line: '#fb7185',
    volume: 'rgba(251,113,133,.23)',
  },
  neutral: {
    body: '#94a3b8',
    wick: '#94a3b8',
    line: '#94a3b8',
    volume: 'rgba(148,163,184,.20)',
  },
});

let appliedSymbolPriceFormat = '';
let liveCandleOpenPrice = null;
let liveCandleDirection = 'neutral';
let liveCandleRow = null;
let liveCandleGlowResizeObserver = null;
let chartViewportUserControlled = false;
let chartViewportLastInteractionAt = 0;
let chartViewportGuardInstalled = false;

function quotePrecision(quote) {
  const explicit = Number(quote?.digits ?? quote?.precision);
  if (Number.isInteger(explicit) && explicit >= 0 && explicit <= 8) return explicit;

  const point = Number(quote?.point ?? quote?.tick_size ?? quote?.min_move);
  if (Number.isFinite(point) && point > 0) {
    const digits = Math.max(0, Math.min(8, Math.round(-Math.log10(point))));
    if (Math.abs(point - (10 ** -digits)) <= point * 0.001) return digits;
  }
  return null;
}

function symbolPriceSpec(symbol = state.symbol, quote = null) {
  const normalized = String(symbol || '').toUpperCase();
  const fallback = SYMBOL_PRICE_FORMATS[normalized] || { precision: 4, minMove: 0.0001 };
  const precision = quotePrecision(quote) ?? fallback.precision;
  return { precision, minMove: 10 ** -precision };
}

function symbolPricePrecision(symbol = state.symbol, quote = null) {
  return symbolPriceSpec(symbol, quote).precision;
}

function liveDirectionForPrice(price) {
  const current = Number(price);
  const open = Number(liveCandleOpenPrice);
  if (!Number.isFinite(current) || !Number.isFinite(open)) return 'neutral';
  if (current > open) return 'bullish';
  if (current < open) return 'bearish';
  return 'neutral';
}

function ensureLiveCandleGlow() {
  let glow = document.getElementById('liveCandleGlow');
  if (glow) return glow;
  const wrap = document.querySelector('.chart-wrap');
  if (!wrap) return null;

  glow = document.createElement('div');
  glow.id = 'liveCandleGlow';
  glow.className = 'live-candle-glow';
  glow.setAttribute('aria-hidden', 'true');
  wrap.appendChild(glow);

  chart.timeScale().subscribeVisibleLogicalRangeChange(positionLiveCandleGlow);
  liveCandleGlowResizeObserver = new ResizeObserver(positionLiveCandleGlow);
  liveCandleGlowResizeObserver.observe(wrap);
  return glow;
}

function hideLiveCandleGlow() {
  const glow = document.getElementById('liveCandleGlow');
  if (glow) glow.style.display = 'none';
}

function clearLiveCandleGlow() {
  liveCandleRow = null;
  liveCandleOpenPrice = null;
  liveCandleDirection = 'neutral';
  hideLiveCandleGlow();
}

function positionLiveCandleGlow() {
  const glow = ensureLiveCandleGlow();
  const row = liveCandleRow;
  if (!glow || !row) {
    hideLiveCandleGlow();
    return;
  }

  const x = chart.timeScale().timeToCoordinate(toTime(row.timestamp));
  if (!Number.isFinite(x)) {
    hideLiveCandleGlow();
    return;
  }

  const palette = LIVE_DIRECTION_COLORS[liveCandleDirection] || LIVE_DIRECTION_COLORS.neutral;
  glow.style.color = palette.body;
  glow.dataset.direction = liveCandleDirection;

  if (state.chartType === 'line') {
    const y = liveLine.priceToCoordinate(Number(row.close));
    if (!Number.isFinite(y)) {
      hideLiveCandleGlow();
      return;
    }
    glow.classList.add('line-mode');
    glow.style.left = `${x}px`;
    glow.style.top = `${y}px`;
    glow.style.width = '11px';
    glow.style.height = '11px';
    glow.style.transform = 'translate(-50%, -50%)';
  } else {
    const highY = liveCandles.priceToCoordinate(Number(row.high));
    const lowY = liveCandles.priceToCoordinate(Number(row.low));
    if (!Number.isFinite(highY) || !Number.isFinite(lowY)) {
      hideLiveCandleGlow();
      return;
    }
    const top = Math.min(highY, lowY) - 3;
    const height = Math.max(13, Math.abs(lowY - highY) + 6);
    glow.classList.remove('line-mode');
    glow.style.left = `${x - 5}px`;
    glow.style.top = `${top}px`;
    glow.style.width = '10px';
    glow.style.height = `${height}px`;
    glow.style.transform = 'none';
  }
  glow.style.display = 'block';
}

function updateLiveDirectionVisual(row = null, quote = null) {
  if (row && Number.isFinite(Number(row.open))) {
    liveCandleOpenPrice = Number(row.open);
    liveCandleRow = {
      ...row,
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
    };
  } else if (quote && liveCandleRow && Number.isFinite(Number(quote.price))) {
    const price = Number(quote.price);
    liveCandleRow = {
      ...liveCandleRow,
      close: price,
      high: Math.max(Number(liveCandleRow.high), price),
      low: Math.min(Number(liveCandleRow.low), price),
    };
  }

  const price = Number(row?.close ?? quote?.price ?? liveCandleRow?.close ?? state.quote?.price);
  const direction = liveDirectionForPrice(price);
  const palette = LIVE_DIRECTION_COLORS[direction];
  liveCandleDirection = direction;

  liveCandles.applyOptions({
    upColor: LIVE_DIRECTION_COLORS.bullish.body,
    downColor: LIVE_DIRECTION_COLORS.bearish.body,
    borderUpColor: LIVE_DIRECTION_COLORS.bullish.body,
    borderDownColor: LIVE_DIRECTION_COLORS.bearish.body,
    wickUpColor: LIVE_DIRECTION_COLORS.bullish.wick,
    wickDownColor: LIVE_DIRECTION_COLORS.bearish.wick,
  });
  liveLine.applyOptions({ color: palette.line });
  if (state.priceLine) {
    try { state.priceLine.applyOptions({ color: palette.line }); } catch (_) {}
  }
  requestAnimationFrame(positionLiveCandleGlow);
  return palette;
}

function markChartViewportInteraction() {
  chartViewportUserControlled = true;
  chartViewportLastInteractionAt = Date.now();
}

function resetChartViewportInteraction() {
  chartViewportUserControlled = false;
  chartViewportLastInteractionAt = 0;
}

function installChartViewportGuard() {
  if (chartViewportGuardInstalled) return;
  const chartNode = document.getElementById('chart');
  if (!chartNode) return;
  chartViewportGuardInstalled = true;

  let pointerActive = false;
  chartNode.addEventListener('wheel', markChartViewportInteraction, { passive: true });
  chartNode.addEventListener('pointerdown', () => {
    pointerActive = true;
  });
  chartNode.addEventListener('pointermove', event => {
    if (pointerActive && (event.buttons > 0 || event.pressure > 0)) {
      markChartViewportInteraction();
    }
  });
  window.addEventListener('pointerup', () => {
    pointerActive = false;
  });
  window.addEventListener('pointercancel', () => {
    pointerActive = false;
  });
  chartNode.addEventListener('touchstart', event => {
    if (event.touches.length > 1) markChartViewportInteraction();
  }, { passive: true });
  chartNode.addEventListener('touchmove', markChartViewportInteraction, { passive: true });
}

function installReplayBacktestAccess() {
  const toolbar = document.querySelector('.chart-toolbar');
  const replayTab = document.querySelector('[data-tab="replay"]');
  const replayContent = document.querySelector('[data-content="replay"]');
  if (!toolbar || !replayTab || !replayContent) return;

  replayTab.textContent = 'Backtest';
  replayTab.title = 'Open historical quick replay';

  if (!document.getElementById('openReplayBacktest')) {
    const button = document.createElement('button');
    button.id = 'openReplayBacktest';
    button.type = 'button';
    button.className = 'ghost-button replay-launch-button';
    button.textContent = 'Backtest';
    button.title = 'Open the historical replay panel';
    const refresh = document.getElementById('refreshForecast');
    toolbar.insertBefore(button, refresh || null);
    button.addEventListener('click', () => {
      setTab('replay');
      document.querySelector('.workspace')?.classList.add('replay-expanded');
      document.getElementById('analysisView')?.classList.add('replay-highlight');
      setTimeout(() => document.getElementById('analysisView')?.classList.remove('replay-highlight'), 900);
    });
  }

  if (!document.getElementById('replayModelDisclosure')) {
    const disclosure = document.createElement('div');
    disclosure.id = 'replayModelDisclosure';
    disclosure.className = 'replay-model-disclosure';
    disclosure.innerHTML = '<strong>Quick replay baseline</strong><span>This currently tests a no-lookahead 20-candle drift strategy—not Kronos inference. It validates the replay pipeline, but it is not a model backtest.</span>';
    replayContent.prepend(disclosure);
  }

  const runButton = document.getElementById('runReplay');
  if (runButton) {
    runButton.textContent = 'Run quick replay';
    runButton.title = 'Runs the current drift baseline on historical candles';
  }

  document.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelector('.workspace')?.classList.toggle('replay-expanded', button.dataset.tab === 'replay');
    });
  });
}

function installChartScaleStyles() {
  if (document.getElementById('traidChartScaleStyles')) return;
  const style = document.createElement('style');
  style.id = 'traidChartScaleStyles';
  style.textContent = `
    .chart-toolbar {
      position:relative !important;
      overflow:visible !important;
      z-index:50 !important;
    }
    .forecast-history-controls,
    .forecast-lock-wrap {
      position:relative !important;
      z-index:51 !important;
    }
    .forecast-lock-menu {
      z-index:100 !important;
    }
    .chart-status-row {
      position:relative;
      z-index:4;
    }
    .swatch.active-candle {
      background:linear-gradient(90deg,#2dd4bf,#fb7185) !important;
      box-shadow:0 0 7px rgba(45,212,191,.30),0 0 7px rgba(251,113,133,.24);
    }
    .live-candle-glow {
      position:absolute;
      z-index:9;
      display:none;
      pointer-events:none;
      border:1px solid currentColor;
      border-radius:4px;
      background:transparent;
      box-shadow:0 0 5px currentColor,0 0 12px currentColor,0 0 22px currentColor;
      animation:traidLiveCandleGlow 1.75s ease-in-out infinite;
      will-change:opacity,filter;
    }
    .live-candle-glow.line-mode { border-radius:999px; background:currentColor; }
    @keyframes traidLiveCandleGlow {
      0%,100% { opacity:.30; filter:brightness(.88); }
      50% { opacity:.82; filter:brightness(1.45); }
    }
    .replay-launch-button { white-space:nowrap; }
    .workspace.replay-expanded {
      grid-template-rows:minmax(300px,.85fr) minmax(320px,1.15fr) !important;
    }
    #analysisView.replay-highlight {
      border-color:rgba(96,165,250,.56);
      box-shadow:0 0 0 2px rgba(59,130,246,.12),var(--shadow);
    }
    .replay-model-disclosure {
      display:grid;
      gap:4px;
      margin-bottom:11px;
      padding:10px 12px;
      border:1px solid rgba(251,191,36,.20);
      border-radius:10px;
      background:rgba(251,191,36,.055);
    }
    .replay-model-disclosure strong { color:#fde68a; font-size:10px; }
    .replay-model-disclosure span { color:#aab3cc; font-size:9px; line-height:1.5; }
    .chart-side-dimmer {
      z-index:4 !important;
      background:rgba(3,6,18,.24) !important;
      -webkit-backdrop-filter:saturate(40%) brightness(68%) !important;
      backdrop-filter:saturate(40%) brightness(68%) !important;
      transition:opacity 250ms ease !important;
    }
    .forecast-boundary-separator {
      z-index:6 !important;
    }
    .forecast-boundary-label {
      z-index:7 !important;
    }
    .forecast-intelligence-overlay {
      bottom:46px !important;
      z-index:30 !important;
    }
    .forecast-intelligence-card {
      position:relative;
      z-index:31 !important;
    }
    @media (prefers-reduced-motion:reduce) {
      .live-candle-glow { animation:none; opacity:.48; }
    }
    @media (max-width:900px) {
      .forecast-intelligence-overlay { bottom:42px !important; }
      .workspace.replay-expanded { grid-template-rows:minmax(280px,.8fr) minmax(300px,1.2fr) !important; }
    }
    @media (max-width:540px) {
      .forecast-intelligence-overlay { bottom:38px !important; }
      .replay-launch-button { display:none; }
    }
  `;
  document.head.appendChild(style);
}

function applySymbolPriceFormat(symbol = state.symbol, quote = null) {
  const normalized = String(symbol || '').toUpperCase();
  const spec = symbolPriceSpec(normalized, quote);
  const key = `${normalized}:${spec.precision}:${spec.minMove}`;
  if (key === appliedSymbolPriceFormat) return spec;

  const options = {
    priceFormat: {
      type: 'price',
      precision: spec.precision,
      minMove: spec.minMove,
    },
  };
  [
    marketCandles, marketLine, liveCandles, liveLine,
    olderCandles, previousCandles, forecastCandles,
    olderLine, previousLine, forecastLine, p10Line, p90Line,
  ].forEach(series => {
    try { series.applyOptions(options); } catch (error) {
      console.warn('Could not apply symbol price precision.', error);
    }
  });
  appliedSymbolPriceFormat = key;
  return spec;
}

function clearPriceSeriesForMarketSwitch() {
  [
    marketCandles, marketLine, liveCandles, liveLine,
    olderCandles, previousCandles, forecastCandles,
    olderLine, previousLine, forecastLine, p10Line, p90Line,
  ].forEach(series => {
    try { series.setData([]); } catch (_) {}
  });
  [marketVolume, olderVolume, previousVolume, forecastVolume].forEach(series => {
    try { series.setData([]); } catch (_) {}
  });
}

function resetChartForMarketSwitch(symbol = state.symbol) {
  if (state.priceLine) {
    try { marketCandles.removePriceLine(state.priceLine); } catch (_) {}
    state.priceLine = null;
  }

  clearPriceSeriesForMarketSwitch();
  clearLiveCandleGlow();
  try { clearPositionEntryVisuals(); } catch (_) {}
  try { hideForecastBoundary(); } catch (_) {}
  try { clearChartSideDimming(); } catch (_) {}

  state.quote = null;
  state.firstClose = null;
  state.lastQuoteAt = 0;
  state.lastCompletedCandleTime = 0;
  state.currentCandleTime = 0;
  appliedSymbolPriceFormat = '';
  applySymbolPriceFormat(symbol);
  resetChartViewportInteraction();

  try {
    chart.priceScale('right').applyOptions({
      autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.22 },
    });
  } catch (_) {}
  try { chart.timeScale().resetTimeScale?.(); } catch (_) {}

  // The live socket can paint the new forming candle before the much slower
  // forecast request completes. Restore completed history independently so the
  // chart never sits on one candle while Kronos is still processing.
  setTimeout(() => {
    if (typeof syncCompletedHistoryToLive === 'function') {
      syncCompletedHistoryToLive({ force: true });
    }
  }, 0);
}

function fitCurrentMarket({ fitTime = true, forceTime = false } = {}) {
  applySymbolPriceFormat(state.symbol, state.quote);
  const rescale = () => {
    try {
      chart.priceScale('right').applyOptions({
        autoScale: true,
        scaleMargins: { top: 0.08, bottom: 0.22 },
      });
    } catch (_) {}
    positionLiveCandleGlow();
  };

  rescale();
  const preserveUserViewport = chartViewportUserControlled && !forceTime;
  if (fitTime && !preserveUserViewport) {
    try { chart.timeScale().fitContent(); } catch (_) {}
  }
  requestAnimationFrame(() => {
    rescale();
    requestAnimationFrame(rescale);
  });
}

installChartScaleStyles();
installChartViewportGuard();
applySymbolPriceFormat(state.symbol);
ensureLiveCandleGlow();
updateLiveDirectionVisual();
installReplayBacktestAccess();
