(() => {
  const wrapperUrl = document.currentScript.src;
  const originalLoaderUrl = new URL('./app-loader.js', wrapperUrl).href;
  const enhancementUrl = new URL('./chart-enhancements-runtime.js', wrapperUrl).href;

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
  ])
    .then(([original, chartEnhancements]) => {
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
        'let replaySeries;\\n\\n' + ${JSON.stringify("let forecastBoundaryTimestamp = null;
let forecastBoundaryCoordinate = null;
let entrySeriesMarkers = null;
let positionEntryLines = [];
let chartEnhancementResizeObserver = null;

function installChartEnhancementStyles() {
  if (document.getElementById('traidChartEnhancementStyles')) return;
  const style = document.createElement('style');
  style.id = 'traidChartEnhancementStyles';
  style.textContent = `
    .forecast-boundary-separator {
      position:absolute;
      top:0;
      bottom:0;
      width:2px;
      z-index:7;
      display:none;
      pointer-events:none;
      background:linear-gradient(180deg,#38bdf8 0%,#6366f1 46%,#a855f7 100%);
      box-shadow:0 0 8px rgba(56,189,248,.9),0 0 18px rgba(99,102,241,.75),0 0 30px rgba(168,85,247,.55);
      animation:traidForecastBoundaryPulse 1.8s ease-in-out infinite;
    }
    .forecast-boundary-separator::after {
      content:'REAL  |  FORECAST';
      position:absolute;
      top:10px;
      left:50%;
      transform:translateX(-50%);
      padding:3px 7px;
      border:1px solid rgba(129,140,248,.36);
      border-radius:999px;
      background:rgba(7,11,27,.88);
      color:#c4b5fd;
      font:800 8px/1 system-ui,sans-serif;
      letter-spacing:.08em;
      white-space:nowrap;
    }
    .chart-side-dimmer {
      position:absolute;
      top:0;
      bottom:0;
      z-index:3;
      opacity:0;
      pointer-events:none;
      background:rgba(7,11,27,.10);
      -webkit-backdrop-filter:saturate(67%) opacity(67%);
      backdrop-filter:saturate(67%) opacity(67%);
      transition:opacity 140ms ease;
    }
    .chart-side-dimmer.active { opacity:1; }
    @keyframes traidForecastBoundaryPulse {
      0%,100% {
        filter:hue-rotate(0deg) brightness(1);
        box-shadow:0 0 7px rgba(56,189,248,.85),0 0 17px rgba(99,102,241,.68),0 0 28px rgba(168,85,247,.48);
      }
      50% {
        filter:hue-rotate(34deg) brightness(1.35);
        box-shadow:0 0 12px rgba(56,189,248,1),0 0 26px rgba(99,102,241,.95),0 0 42px rgba(168,85,247,.78);
      }
    }
    @media (prefers-reduced-motion:reduce) {
      .forecast-boundary-separator { animation:none; }
      .chart-side-dimmer { transition:none; }
    }
  `;
  document.head.appendChild(style);
}

function chartEnhancementNodes() {
  return {
    wrap: document.querySelector('.chart-wrap'),
    chartNode: document.getElementById('chart'),
    separator: document.getElementById('forecastBoundarySeparator'),
    leftDimmer: document.getElementById('forecastLeftDimmer'),
    rightDimmer: document.getElementById('forecastRightDimmer'),
  };
}

function clearChartSideDimming() {
  const { leftDimmer, rightDimmer } = chartEnhancementNodes();
  leftDimmer?.classList.remove('active');
  rightDimmer?.classList.remove('active');
}

function positionForecastBoundary() {
  const { wrap, separator, leftDimmer, rightDimmer } = chartEnhancementNodes();
  if (!wrap || !separator || !leftDimmer || !rightDimmer || !forecastBoundaryTimestamp) {
    if (separator) separator.style.display = 'none';
    clearChartSideDimming();
    forecastBoundaryCoordinate = null;
    return;
  }

  const forecastX = chart.timeScale().timeToCoordinate(forecastBoundaryTimestamp);
  if (forecastX == null || !Number.isFinite(forecastX)) {
    separator.style.display = 'none';
    clearChartSideDimming();
    forecastBoundaryCoordinate = null;
    return;
  }

  const realTimestamp = state.currentCandleTime || state.lastCompletedCandleTime || null;
  const realX = realTimestamp ? chart.timeScale().timeToCoordinate(realTimestamp) : null;
  let coordinate = forecastX - 4;
  if (realX != null && Number.isFinite(realX) && realX < forecastX) {
    coordinate = (realX + forecastX) / 2;
  }
  coordinate = Math.max(0, Math.min(wrap.clientWidth, coordinate));
  forecastBoundaryCoordinate = coordinate;

  separator.style.left = `${coordinate}px`;
  separator.style.display = 'block';
  leftDimmer.style.left = '0';
  leftDimmer.style.width = `${coordinate}px`;
  rightDimmer.style.left = `${coordinate}px`;
  rightDimmer.style.right = '0';
}

function updateForecastBoundary(rows = []) {
  forecastBoundaryTimestamp = Array.isArray(rows) && rows.length
    ? toTime(rows[0].timestamp)
    : null;
  requestAnimationFrame(positionForecastBoundary);
}

function installChartEnhancements() {
  installChartEnhancementStyles();
  const wrap = document.querySelector('.chart-wrap');
  if (!wrap || document.getElementById('forecastBoundarySeparator')) return;

  const leftDimmer = document.createElement('div');
  leftDimmer.id = 'forecastLeftDimmer';
  leftDimmer.className = 'chart-side-dimmer';

  const rightDimmer = document.createElement('div');
  rightDimmer.id = 'forecastRightDimmer';
  rightDimmer.className = 'chart-side-dimmer';

  const separator = document.createElement('div');
  separator.id = 'forecastBoundarySeparator';
  separator.className = 'forecast-boundary-separator';

  wrap.append(leftDimmer, rightDimmer, separator);

  wrap.addEventListener('mousemove', event => {
    if (forecastBoundaryCoordinate == null) return;
    const rectangle = wrap.getBoundingClientRect();
    const pointerX = event.clientX - rectangle.left;
    if (pointerX < forecastBoundaryCoordinate) {
      leftDimmer.classList.remove('active');
      rightDimmer.classList.add('active');
    } else {
      rightDimmer.classList.remove('active');
      leftDimmer.classList.add('active');
    }
  });
  wrap.addEventListener('mouseleave', clearChartSideDimming);

  chart.timeScale().subscribeVisibleLogicalRangeChange(positionForecastBoundary);
  chartEnhancementResizeObserver = new ResizeObserver(positionForecastBoundary);
  chartEnhancementResizeObserver.observe(wrap);
}

function clearPositionEntryVisuals() {
  for (const line of positionEntryLines) {
    try { marketCandles.removePriceLine(line); } catch (_) {}
  }
  positionEntryLines = [];
  if (entrySeriesMarkers?.setMarkers) {
    try { entrySeriesMarkers.setMarkers([]); } catch (_) {}
  }
}

function renderPositionEntryVisuals(positions = []) {
  clearPositionEntryVisuals();
  const relevant = (Array.isArray(positions) ? positions : [])
    .filter(position => position.symbol === state.symbol)
    .filter(position => Number.isFinite(Number(position.open_price)));
  if (!relevant.length) return;

  const timeframeSeconds = TIMEFRAME_SECONDS[state.timeframe] || 60;
  const markers = relevant
    .map(position => {
      const openedAt = Number(position.opened_at);
      if (!Number.isFinite(openedAt)) return null;
      const isLong = position.side === 'buy';
      return {
        time: Math.floor(openedAt / timeframeSeconds) * timeframeSeconds,
        position: isLong ? 'belowBar' : 'aboveBar',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        color: isLong ? '#2dd4bf' : '#fb7185',
        text: `${isLong ? 'LONG' : 'SHORT'} ${position.volume ?? ''}`.trim(),
      };
    })
    .filter(Boolean)
    .sort((first, second) => first.time - second.time);

  if (typeof LightweightCharts.createSeriesMarkers === 'function') {
    try {
      if (!entrySeriesMarkers) {
        entrySeriesMarkers = LightweightCharts.createSeriesMarkers(marketCandles, markers);
      } else {
        entrySeriesMarkers.setMarkers(markers);
      }
    } catch (error) {
      console.warn('Could not render position arrows.', error);
    }
  }

  for (const position of relevant) {
    const isLong = position.side === 'buy';
    try {
      positionEntryLines.push(
        marketCandles.createPriceLine({
          price: Number(position.open_price),
          color: isLong ? 'rgba(45,212,191,.88)' : 'rgba(251,113,133,.88)',
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `${isLong ? '▲ LONG' : '▼ SHORT'} ${position.volume ?? ''}`.trim(),
        }),
      );
    } catch (error) {
      console.warn('Could not render a position entry line.', error);
    }
  }
}

installChartEnhancements();
")} + '\\nfunction applyChartType() {'
      );

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
