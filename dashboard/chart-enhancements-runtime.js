let forecastBoundaryTimestamp = null;
let forecastBoundaryCoordinate = null;
let entrySeriesMarkers = null;
let positionEntryLines = [];
let chartEnhancementResizeObserver = null;
let historyContinuitySyncInFlight = false;
let historyContinuityTimer = null;

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
      animation:traidForecastBoundaryPulse 4.5s ease-in-out infinite;
    }
    .forecast-boundary-label {
      position:absolute;
      top:12px;
      z-index:8;
      display:none;
      pointer-events:none;
      color:#fff;
      font:700 9px/1 system-ui,sans-serif;
      letter-spacing:.12em;
      white-space:nowrap;
    }
    .forecast-boundary-label.real {
      transform:translateX(-100%);
      text-align:right;
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
    separator: document.getElementById('forecastBoundarySeparator'),
    realLabel: document.getElementById('forecastBoundaryRealLabel'),
    forecastLabel: document.getElementById('forecastBoundaryForecastLabel'),
    leftDimmer: document.getElementById('forecastLeftDimmer'),
    rightDimmer: document.getElementById('forecastRightDimmer'),
  };
}

function clearChartSideDimming() {
  const { leftDimmer, rightDimmer } = chartEnhancementNodes();
  leftDimmer?.classList.remove('active');
  rightDimmer?.classList.remove('active');
}

function hideForecastBoundary() {
  const { separator, realLabel, forecastLabel } = chartEnhancementNodes();
  if (separator) separator.style.display = 'none';
  if (realLabel) realLabel.style.display = 'none';
  if (forecastLabel) forecastLabel.style.display = 'none';
}

function positionForecastBoundary() {
  const {
    wrap,
    separator,
    realLabel,
    forecastLabel,
    leftDimmer,
    rightDimmer,
  } = chartEnhancementNodes();

  if (!wrap || !separator || !realLabel || !forecastLabel || !leftDimmer
      || !rightDimmer || !forecastBoundaryTimestamp) {
    hideForecastBoundary();
    clearChartSideDimming();
    forecastBoundaryCoordinate = null;
    return;
  }

  const forecastX = chart.timeScale().timeToCoordinate(forecastBoundaryTimestamp);
  if (forecastX == null || !Number.isFinite(forecastX)) {
    hideForecastBoundary();
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
  realLabel.style.left = `${Math.max(0, coordinate - 10)}px`;
  realLabel.style.display = 'block';
  forecastLabel.style.left = `${Math.min(wrap.clientWidth, coordinate + 10)}px`;
  forecastLabel.style.display = 'block';
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

  const realLabel = document.createElement('span');
  realLabel.id = 'forecastBoundaryRealLabel';
  realLabel.className = 'forecast-boundary-label real';
  realLabel.textContent = 'REAL';

  const forecastLabel = document.createElement('span');
  forecastLabel.id = 'forecastBoundaryForecastLabel';
  forecastLabel.className = 'forecast-boundary-label forecast';
  forecastLabel.textContent = 'FORECAST';

  wrap.append(leftDimmer, rightDimmer, separator, realLabel, forecastLabel);

  wrap.addEventListener('mousemove', event => {
    if (forecastBoundaryCoordinate == null) return;
    const pointerX = event.clientX - wrap.getBoundingClientRect().left;
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
  const markers = relevant.map(position => {
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
  }).filter(Boolean).sort((first, second) => first.time - second.time);

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
      positionEntryLines.push(marketCandles.createPriceLine({
        price: Number(position.open_price),
        color: isLong ? 'rgba(45,212,191,.88)' : 'rgba(251,113,133,.88)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${isLong ? '▲ LONG' : '▼ SHORT'} ${position.volume ?? ''}`.trim(),
      }));
    } catch (error) {
      console.warn('Could not render a position entry line.', error);
    }
  }
}

async function syncCompletedHistoryToLive({ force = false } = {}) {
  if (historyContinuitySyncInFlight) return;

  const symbol = state.symbol;
  const timeframe = state.timeframe;
  const requestId = state.marketRequestId;
  const expectedSeconds = TIMEFRAME_SECONDS[timeframe] || 60;
  const completedTime = Number(state.lastCompletedCandleTime || 0);
  const liveTime = Number(state.currentCandleTime || 0);
  const initialHistoryLoad = completedTime <= 0;
  const missingHistory = liveTime > 0 && initialHistoryLoad;
  const hasGap = liveTime > 0 && (
    missingHistory || liveTime - completedTime > expectedSeconds * 1.1
  );

  if (!force && !hasGap) return;
  historyContinuitySyncInFlight = true;

  try {
    const payload = await api(`/v1/candles/${symbol}?timeframe=${timeframe}&limit=400`);
    if (!currentMarketRequest(requestId, symbol, timeframe)) return;

    const rows = Array.isArray(payload?.candles) ? payload.candles : [];
    if (rows.length < 2 || !rowsMatchTimeframe(rows, timeframe)) return;

    const latestCompletedTime = toTime(rows.at(-1)?.timestamp);
    if (!Number.isFinite(latestCompletedTime)) return;
    if (latestCompletedTime < Number(state.lastCompletedCandleTime || 0)) return;

    setHistory(rows);
    renderPriorForecasts();
    positionForecastBoundary();
    if (typeof fitCurrentMarket === 'function') {
      // A first history load may fit the chart only until the user zooms or pans.
      // Later gap repairs update price autoscaling without touching the time axis.
      fitCurrentMarket({ fitTime: initialHistoryLoad });
    }
  } catch (error) {
    console.warn('Could not backfill completed candles.', error);
  } finally {
    historyContinuitySyncInFlight = false;
  }
}

function installHistoryContinuityGuard() {
  if (historyContinuityTimer) return;
  setTimeout(() => syncCompletedHistoryToLive({ force: true }), 500);
  historyContinuityTimer = setInterval(() => syncCompletedHistoryToLive(), 1250);
}

installChartEnhancements();
installHistoryContinuityGuard();
