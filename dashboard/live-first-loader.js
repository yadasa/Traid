(() => {
  const wrapperUrl = document.currentScript.src;
  const originalLoaderUrl = new URL('./app-loader.js', wrapperUrl).href;
  const enhancementUrl = new URL('./chart-enhancements-runtime.js', wrapperUrl).href;
  const scaleRuntimeUrl = new URL('./chart-scale-runtime.js', wrapperUrl).href;

  const additionalMarkets = [
    { symbol: 'EURUSD', label: 'Euro / U.S. Dollar', shortLabel: 'Euro / U.S. Dollar' },
    { symbol: 'USDJPY', label: 'U.S. Dollar / Japanese Yen', shortLabel: 'Dollar / Yen' },
  ];

  function installAdditionalMarkets() {
    const select = document.getElementById('symbolSelect');
    const watchlist = document.getElementById('watchlistItems');

    for (const market of additionalMarkets) {
      if (select && !select.querySelector(`option[value="${market.symbol}"]`)) {
        const option = document.createElement('option');
        option.value = market.symbol;
        option.textContent = market.symbol;
        const before = select.querySelector('option[value="NAS100"]');
        select.insertBefore(option, before || null);
      }

      if (watchlist && !watchlist.querySelector(`[data-symbol="${market.symbol}"]`)) {
        const button = document.createElement('button');
        button.className = 'watch-item';
        button.dataset.symbol = market.symbol;
        button.innerHTML = `<span><strong>${market.symbol}</strong><small>${market.shortLabel}</small></span><span class="watch-price" data-watch-price="${market.symbol}">—</span>`;
        const before = watchlist.querySelector('[data-symbol="NAS100"]');
        watchlist.insertBefore(button, before || null);
      }
    }

    document.querySelectorAll('.chart-status-row .legend span').forEach(node => {
      if (node.textContent.includes('Previous · 67%')) node.lastChild.textContent = 'Previous · 45%';
      if (node.textContent.includes('Older · 33%')) node.lastChild.textContent = 'Older · 22%';
    });
  }

  const fail = error => {
    console.error('Traid live-first loader failed', error);
    const node = document.createElement('div');
    node.style.cssText = 'position:fixed;inset:16px;z-index:99999;padding:16px;border:1px solid #fb7185;background:#170b18;color:#fecdd3;font:14px/1.5 system-ui;border-radius:12px;white-space:pre-wrap';
    node.textContent = `Dashboard startup failed: ${error.message}`;
    document.body.appendChild(node);
  };

  installAdditionalMarkets();

  Promise.all([
    fetch(originalLoaderUrl, { cache: 'no-store' }).then(response => {
      if (!response.ok) throw new Error(`Could not load app-loader.js (${response.status})`);
      return response.text();
    }),
    fetch(enhancementUrl, { cache: 'no-store' }).then(response => {
      if (!response.ok) throw new Error(`Could not load chart-enhancements-runtime.js (${response.status})`);
      return response.text();
    }),
    fetch(scaleRuntimeUrl, { cache: 'no-store' }).then(response => {
      if (!response.ok) throw new Error(`Could not load chart-scale-runtime.js (${response.status})`);
      return response.text();
    }),
  ])
    .then(([original, chartEnhancements, chartScaleRuntime]) => {
      let source = original;

      // The fetched loader is injected as inline code, so preserve its original
      // URL explicitly for relative app.js and forecast-intelligence.js requests.
      const loaderMarker = 'const loaderUrl = document.currentScript.src;';
      if (!source.includes(loaderMarker)) {
        throw new Error('Could not locate the app-loader URL marker.');
      }
      source = source.replace(
        loaderMarker,
        `const loaderUrl = ${JSON.stringify(originalLoaderUrl)};`,
      );

      // Patch the locally served app.js at runtime so users keep local fixes while
      // the two additional forex markets receive labels throughout the dashboard.
      const appSourceMarker = '      let source = original;\n';
      if (!source.includes(appSourceMarker)) {
        throw new Error('Could not locate the app.js source marker.');
      }
      const namesPatch = `
      source = source.replace(
        "const names = { XAUUSD: 'Gold / U.S. Dollar', XAGUSD: 'Silver / U.S. Dollar', NAS100: 'Nasdaq 100 CFD', SPX500: 'S&P 500 CFD' };",
        "const names = { XAUUSD: 'Gold / U.S. Dollar', XAGUSD: 'Silver / U.S. Dollar', EURUSD: 'Euro / U.S. Dollar', USDJPY: 'U.S. Dollar / Japanese Yen', NAS100: 'Nasdaq 100 CFD', SPX500: 'S&P 500 CFD' };"
      );
`;
      source = source.replace(
        appSourceMarker,
        `${appSourceMarker}${namesPatch}`,
      );

      // Open the selected market WebSocket before forecast generation. Kronos and
      // multi-timeframe context can work in the background without pausing ticks.
      const forecastWaitMarker = `  try {\n    const [candlesPayload, forecastHistory, health] = await Promise.all([`;
      if (!source.includes(forecastWaitMarker)) {
        throw new Error('Could not locate the market-load forecast marker.');
      }
      source = source.replace(
        forecastWaitMarker,
        `  connectStream(requestedSymbol, requestedTimeframe, requestId);\n\n${forecastWaitMarker}`,
      );

      // Do not replace the already-live connection after inference finishes.
      const lateConnectMarker = `    connectStream(requestedSymbol, requestedTimeframe, requestId);\n    setFeed(true,`;
      if (!source.includes(lateConnectMarker)) {
        throw new Error('Could not locate the late WebSocket connection marker.');
      }
      source = source.replace(
        lateConnectMarker,
        `    setFeed(true,`,
      );

      // Run these transformations after app-loader has produced its final app.js
      // source. This keeps the visual layer compatible with its chart race fixes.
      const finalScriptMarker = "      const script = document.createElement('script');";
      if (!source.includes(finalScriptMarker)) {
        throw new Error('Could not locate the final app script marker.');
      }

      const finalAppPatches = `
      const opacityReplacements = [
        ['rgba(119,157,205,.67)', 'rgba(119,157,205,.45)'],
        ['rgba(147,118,202,.67)', 'rgba(147,118,202,.45)'],
        ['rgba(132,169,214,.67)', 'rgba(132,169,214,.45)'],
        ['rgba(160,132,214,.67)', 'rgba(160,132,214,.45)'],
        ['rgba(139,131,207,.67)', 'rgba(139,131,207,.45)'],
        ['rgba(143,151,162,.33)', 'rgba(143,151,162,.22)'],
        ['rgba(152,139,168,.33)', 'rgba(152,139,168,.22)'],
        ['rgba(151,158,168,.33)', 'rgba(151,158,168,.22)'],
        ['rgba(160,148,175,.33)', 'rgba(160,148,175,.22)'],
        ['rgba(148,145,166,.33)', 'rgba(148,145,166,.22)'],
        ['rgba(132,128,165,.21)', 'rgba(132,128,165,.14)'],
        ['rgba(145,145,155,.105)', 'rgba(145,145,155,.07)'],
      ];
      for (const [before, after] of opacityReplacements) source = source.replaceAll(before, after);

      const chartInsertionMarker = 'let replaySeries;\\n\\nfunction applyChartType() {';
      if (!source.includes(chartInsertionMarker)) {
        throw new Error('Could not locate the chart enhancement insertion point.');
      }
      source = source.replace(
        chartInsertionMarker,
        'let replaySeries;\\n\\n' + ${JSON.stringify(chartEnhancements)} + '\\n' + ${JSON.stringify(chartScaleRuntime)} + '\\nfunction applyChartType() {'
      );

      const quoteDigitsMarker = "  const digits = +quote.price > 1000 ? 2 : 4;";
      if (!source.includes(quoteDigitsMarker)) {
        throw new Error('Could not locate quote precision handling.');
      }
      source = source.replace(
        quoteDigitsMarker,
        "  applySymbolPriceFormat(state.symbol, quote);\\n  const digits = symbolPricePrecision(state.symbol, quote);"
      );
      source = source.replaceAll(
        'payload.quote.price > 1000 ? 2 : 4',
        'symbolPricePrecision(symbol, payload.quote)'
      );

      const marketResetMarker = "  state.lastStreamAt = 0;\\n  resetProjectionHistory(); setFeed(false, 'Loading market and model…');";
      if (!source.includes(marketResetMarker)) {
        throw new Error('Could not locate market-switch reset point.');
      }
      source = source.replace(
        marketResetMarker,
        "  state.lastStreamAt = 0;\\n  resetChartForMarketSwitch(requestedSymbol);\\n  resetProjectionHistory(); setFeed(false, 'Loading market and model…');"
      );

      const fitMarker = '    if (fit) chart.timeScale().fitContent();';
      if (!source.includes(fitMarker)) {
        throw new Error('Could not locate market fit point.');
      }
      source = source.replace(fitMarker, '    if (fit) fitCurrentMarket();');

      source = source.replace(
        "  renderRevision(revision);\\n  return true;\\n}",
        "  renderRevision(revision);\\n  updateForecastBoundary(next);\\n  return true;\\n}"
      );
      source = source.replace(
        "  state.projectionHistory = []; state.renderedProjection = []; state.projectionAnimationFrame = null;\\n}",
        "  state.projectionHistory = []; state.renderedProjection = []; state.projectionAnimationFrame = null;\\n  updateForecastBoundary([]);\\n}"
      );
      source = source.replace(
        "state.positions = payload.positions || []; state.pending = payload.pending_orders || [];",
        "state.positions = payload.positions || []; state.pending = payload.pending_orders || []; renderPositionEntryVisuals(state.positions);"
      );
      source = source.replace(
        "  document.querySelectorAll('.watch-item').forEach(item => item.classList.toggle('active', item.dataset.symbol === requestedSymbol));",
        "  document.querySelectorAll('.watch-item').forEach(item => item.classList.toggle('active', item.dataset.symbol === requestedSymbol));\\n  renderPositionEntryVisuals(state.positions);"
      );
`;
      source = source.replace(
        finalScriptMarker,
        `${finalAppPatches}\n${finalScriptMarker}`,
      );

      const script = document.createElement('script');
      script.textContent = source;
      document.head.appendChild(script);
    })
    .catch(fail);
})();
