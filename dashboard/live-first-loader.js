(() => {
  const wrapperUrl = document.currentScript.src;
  const originalLoaderUrl = new URL('./app-loader.js', wrapperUrl).href;

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
  }

  const fail = error => {
    console.error('Traid live-first loader failed', error);
    const node = document.createElement('div');
    node.style.cssText = 'position:fixed;inset:16px;z-index:99999;padding:16px;border:1px solid #fb7185;background:#170b18;color:#fecdd3;font:14px/1.5 system-ui;border-radius:12px;white-space:pre-wrap';
    node.textContent = `Dashboard startup failed: ${error.message}`;
    document.body.appendChild(node);
  };

  installAdditionalMarkets();

  fetch(originalLoaderUrl, { cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error(`Could not load app-loader.js (${response.status})`);
      return response.text();
    })
    .then(original => {
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

      // Patch the locally served app.js at runtime so users keep any local fixes
      // while the two new markets receive labels and participate in watchlist
      // refreshes, symbol switching, forecasts, and order tickets.
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

      // Open the selected market WebSocket as soon as lightweight history is being
      // requested. Forecast generation and multi-timeframe context may continue in
      // the background while live ticks keep updating the chart.
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

      const script = document.createElement('script');
      script.textContent = source;
      document.head.appendChild(script);
    })
    .catch(fail);
})();
