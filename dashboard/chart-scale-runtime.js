const SYMBOL_PRICE_FORMATS = Object.freeze({
  XAUUSD: { precision: 2, minMove: 0.01 },
  XAGUSD: { precision: 4, minMove: 0.0001 },
  EURUSD: { precision: 5, minMove: 0.00001 },
  USDJPY: { precision: 3, minMove: 0.001 },
  NAS100: { precision: 2, minMove: 0.01 },
  SPX500: { precision: 2, minMove: 0.01 },
});

const LIVE_DIRECTION_COLORS = Object.freeze({
  bullish: {
    body: '#2dd4bf',
    wick: '#5eead4',
    line: '#14b8a6',
    volume: 'rgba(45,212,191,.24)',
  },
  bearish: {
    body: '#d9467d',
    wick: '#f472b6',
    line: '#db2777',
    volume: 'rgba(217,70,125,.24)',
  },
  neutral: {
    body: '#94a3b8',
    wick: '#cbd5e1',
    line: '#94a3b8',
    volume: 'rgba(148,163,184,.20)',
  },
});

let appliedSymbolPriceFormat = '';
let liveCandleOpenPrice = null;
let liveCandleDirection = 'neutral';
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

function updateLiveDirectionVisual(row = null, quote = null) {
  if (row && Number.isFinite(Number(row.open))) {
    liveCandleOpenPrice = Number(row.open);
  }
  const price = Number(row?.close ?? quote?.price ?? state.quote?.price);
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
    @media (max-width:900px) {
      .forecast-intelligence-overlay { bottom:42px !important; }
    }
    @media (max-width:540px) {
      .forecast-intelligence-overlay { bottom:38px !important; }
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
  try { clearPositionEntryVisuals(); } catch (_) {}
  try { hideForecastBoundary(); } catch (_) {}
  try { clearChartSideDimming(); } catch (_) {}

  state.quote = null;
  state.firstClose = null;
  state.lastQuoteAt = 0;
  state.lastCompletedCandleTime = 0;
  state.currentCandleTime = 0;
  liveCandleOpenPrice = null;
  liveCandleDirection = 'neutral';
  updateLiveDirectionVisual();
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
updateLiveDirectionVisual();
