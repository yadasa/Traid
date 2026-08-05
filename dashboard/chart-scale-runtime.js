const SYMBOL_PRICE_FORMATS = Object.freeze({
  XAUUSD: { precision: 2, minMove: 0.01 },
  XAGUSD: { precision: 4, minMove: 0.0001 },
  EURUSD: { precision: 5, minMove: 0.00001 },
  USDJPY: { precision: 3, minMove: 0.001 },
  NAS100: { precision: 2, minMove: 0.01 },
  SPX500: { precision: 2, minMove: 0.01 },
});

let appliedSymbolPriceFormat = '';

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

function installChartScaleStyles() {
  if (document.getElementById('traidChartScaleStyles')) return;
  const style = document.createElement('style');
  style.id = 'traidChartScaleStyles';
  style.textContent = `
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
  appliedSymbolPriceFormat = '';
  applySymbolPriceFormat(symbol);

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

function fitCurrentMarket({ fitTime = true } = {}) {
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
  if (fitTime) {
    try { chart.timeScale().fitContent(); } catch (_) {}
  }
  requestAnimationFrame(() => {
    rescale();
    requestAnimationFrame(rescale);
  });
}

installChartScaleStyles();
applySymbolPriceFormat(state.symbol);
