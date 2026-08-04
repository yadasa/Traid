let forecastBoundaryTimestamp = null;
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
