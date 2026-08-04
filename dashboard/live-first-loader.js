(() => {
  const wrapperUrl = document.currentScript.src;
  const originalLoaderUrl = new URL('./app-loader.js', wrapperUrl).href;

  const fail = error => {
    console.error('Traid live-first loader failed', error);
    const node = document.createElement('div');
    node.style.cssText = 'position:fixed;inset:16px;z-index:99999;padding:16px;border:1px solid #fb7185;background:#170b18;color:#fecdd3;font:14px/1.5 system-ui;border-radius:12px;white-space:pre-wrap';
    node.textContent = `Dashboard startup failed: ${error.message}`;
    document.body.appendChild(node);
  };

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
