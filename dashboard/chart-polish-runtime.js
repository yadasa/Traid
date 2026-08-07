(() => {
  const STORAGE_KEY = 'traid.chart.timeMode';
  const LOCAL_TIME_ZONE = 'America/Chicago';
  const VALID_TIME_MODES = new Set(['utc', 'local']);
  let timeMode = VALID_TIME_MODES.has(localStorage.getItem(STORAGE_KEY))
    ? localStorage.getItem(STORAGE_KEY)
    : 'utc';
  let initialized = false;
  let glowResizeObserver = null;
  let polishGuardsInstalled = false;

  function chartReady() {
    try {
      return typeof chart !== 'undefined'
        && typeof liveCandles !== 'undefined'
        && typeof state !== 'undefined'
        && typeof toTime === 'function';
    } catch (_) {
      return false;
    }
  }

  function installStyles() {
    if (document.getElementById('traidChartPolishStyles')) return;
    const style = document.createElement('style');
    style.id = 'traidChartPolishStyles';
    style.textContent = `
      #liveCandleGlow { display:none !important; }

      .traid-live-candle-clone {
        position:absolute;
        inset:0;
        z-index:8;
        width:100%;
        height:100%;
        overflow:visible;
        pointer-events:none;
      }
      .traid-live-glow-pulse {
        animation:traidGaussianLivePulse 2.25s ease-in-out infinite;
        will-change:opacity,filter;
        mix-blend-mode:screen;
      }
      @keyframes traidGaussianLivePulse {
        0%,100% { opacity:.24; filter:brightness(.82); }
        50% { opacity:.82; filter:brightness(1.72); }
      }
      .chart-time-mode-button {
        min-width:72px;
        white-space:nowrap;
      }
      @media (prefers-reduced-motion:reduce) {
        .traid-live-glow-pulse { animation:none; opacity:.48; filter:brightness(1.14); }
      }
    `;
    document.head.appendChild(style);
  }

  function cloneNodes() {
    return {
      wrap: document.querySelector('.chart-wrap'),
      chartNode: document.getElementById('chart'),
      svg: document.getElementById('traidLiveCandleClone'),
      wick: document.getElementById('traidLiveCloneWick'),
      body: document.getElementById('traidLiveCloneBody'),
    };
  }

  function ensureClone() {
    let { wrap, svg } = cloneNodes();
    if (!wrap) return null;
    if (svg) return svg;

    const ns = 'http://www.w3.org/2000/svg';
    svg = document.createElementNS(ns, 'svg');
    svg.id = 'traidLiveCandleClone';
    svg.classList.add('traid-live-candle-clone');
    svg.setAttribute('aria-hidden', 'true');
    svg.style.display = 'none';
    svg.style.overflow = 'visible';

    const defs = document.createElementNS(ns, 'defs');
    const filter = document.createElementNS(ns, 'filter');
    filter.id = 'traidLiveCandleGaussian';
    filter.setAttribute('x', '-300%');
    filter.setAttribute('y', '-300%');
    filter.setAttribute('width', '700%');
    filter.setAttribute('height', '700%');
    filter.setAttribute('color-interpolation-filters', 'sRGB');
    const blur = document.createElementNS(ns, 'feGaussianBlur');
    blur.setAttribute('stdDeviation', '4.4');
    filter.appendChild(blur);
    defs.appendChild(filter);
    svg.appendChild(defs);

    const pulse = document.createElementNS(ns, 'g');
    pulse.classList.add('traid-live-glow-pulse');
    const blurred = document.createElementNS(ns, 'g');
    blurred.setAttribute('filter', 'url(#traidLiveCandleGaussian)');

    const wick = document.createElementNS(ns, 'line');
    wick.id = 'traidLiveCloneWick';
    wick.setAttribute('stroke-linecap', 'round');
    wick.setAttribute('stroke-width', '3');

    const body = document.createElementNS(ns, 'rect');
    body.id = 'traidLiveCloneBody';
    body.setAttribute('rx', '2');
    body.setAttribute('ry', '2');

    blurred.append(wick, body);
    pulse.appendChild(blurred);
    svg.appendChild(pulse);
    wrap.appendChild(svg);

    try {
      chart.timeScale().subscribeVisibleLogicalRangeChange(positionClone);
    } catch (_) {}
    glowResizeObserver = new ResizeObserver(positionClone);
    glowResizeObserver.observe(wrap);
    return svg;
  }

  function hideClone() {
    const svg = document.getElementById('traidLiveCandleClone');
    if (svg) svg.style.display = 'none';
  }

  function currentLiveRow() {
    try {
      if (typeof liveCandleRow !== 'undefined' && liveCandleRow) return liveCandleRow;
    } catch (_) {}
    return null;
  }

  function liveColor(row) {
    const open = Number(row?.open);
    const close = Number(row?.close);
    if (!Number.isFinite(open) || !Number.isFinite(close)) return '#94a3b8';
    if (close > open) return '#2dd4bf';
    if (close < open) return '#fb7185';
    return '#94a3b8';
  }

  function positionClone() {
    if (!chartReady()) return;
    const row = currentLiveRow();
    const { wrap, chartNode } = cloneNodes();
    const svg = ensureClone();
    if (!row || !wrap || !chartNode || !svg || state.chartType === 'line') {
      hideClone();
      return;
    }

    const wrapRect = wrap.getBoundingClientRect();
    const chartRect = chartNode.getBoundingClientRect();
    const offsetX = chartRect.left - wrapRect.left;
    const offsetY = chartRect.top - wrapRect.top;
    const x = chart.timeScale().timeToCoordinate(toTime(row.timestamp));
    const highY = liveCandles.priceToCoordinate(Number(row.high));
    const lowY = liveCandles.priceToCoordinate(Number(row.low));
    const openY = liveCandles.priceToCoordinate(Number(row.open));
    const closeY = liveCandles.priceToCoordinate(Number(row.close));
    if (![x, highY, lowY, openY, closeY].every(Number.isFinite)) {
      hideClone();
      return;
    }

    const width = Math.max(1, wrap.clientWidth);
    const height = Math.max(1, wrap.clientHeight);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    let barSpacing = 8;
    try {
      const candidate = Number(chart.timeScale().options?.().barSpacing);
      if (Number.isFinite(candidate) && candidate > 0) barSpacing = candidate;
    } catch (_) {}

    // Lightweight Charts expands candle bodies as bars are zoomed apart. The old
    // 12px cap made a wide candle's glow look like a second wick. Track bar spacing
    // instead so the blurred clone remains the same visual candle at every zoom.
    const sharpBodyWidth = Math.max(4, Math.min(180, barSpacing * .78));
    const glowPadX = Math.max(3, Math.min(10, sharpBodyWidth * .10));
    const glowPadY = 3.5;
    const cx = offsetX + x;
    const wickTop = offsetY + Math.min(highY, lowY);
    const wickBottom = offsetY + Math.max(highY, lowY);
    const sharpBodyTop = offsetY + Math.min(openY, closeY);
    const sharpBodyHeight = Math.max(2, Math.abs(closeY - openY));
    const color = liveColor(row);

    const wick = document.getElementById('traidLiveCloneWick');
    const body = document.getElementById('traidLiveCloneBody');
    if (!wick || !body) return;

    wick.setAttribute('x1', String(cx));
    wick.setAttribute('x2', String(cx));
    wick.setAttribute('y1', String(wickTop));
    wick.setAttribute('y2', String(wickBottom));
    wick.setAttribute('stroke', color);

    body.setAttribute('x', String(cx - sharpBodyWidth / 2 - glowPadX));
    body.setAttribute('y', String(sharpBodyTop - glowPadY));
    body.setAttribute('width', String(sharpBodyWidth + glowPadX * 2));
    body.setAttribute('height', String(sharpBodyHeight + glowPadY * 2));
    body.setAttribute('fill', color);
    body.setAttribute('stroke', color);
    body.setAttribute('stroke-width', '1.5');

    svg.style.display = 'block';
  }

  function allPriceSeries() {
    const found = [];
    const push = candidate => { if (candidate && !found.includes(candidate)) found.push(candidate); };
    try { if (typeof marketCandles !== 'undefined') push(marketCandles); } catch (_) {}
    try { if (typeof marketLine !== 'undefined') push(marketLine); } catch (_) {}
    try { if (typeof liveCandles !== 'undefined') push(liveCandles); } catch (_) {}
    try { if (typeof liveLine !== 'undefined') push(liveLine); } catch (_) {}
    try { if (typeof olderCandles !== 'undefined') push(olderCandles); } catch (_) {}
    try { if (typeof previousCandles !== 'undefined') push(previousCandles); } catch (_) {}
    try { if (typeof forecastCandles !== 'undefined') push(forecastCandles); } catch (_) {}
    try { if (typeof olderLine !== 'undefined') push(olderLine); } catch (_) {}
    try { if (typeof previousLine !== 'undefined') push(previousLine); } catch (_) {}
    try { if (typeof forecastLine !== 'undefined') push(forecastLine); } catch (_) {}
    try { if (typeof p10Line !== 'undefined') push(p10Line); } catch (_) {}
    try { if (typeof p90Line !== 'undefined') push(p90Line); } catch (_) {}
    return found;
  }

  function keepOnlyExplicitLivePriceLabel() {
    for (const series of allPriceSeries()) {
      try {
        series.applyOptions({
          lastValueVisible: false,
          priceLineVisible: false,
        });
      } catch (_) {}
    }
    try {
      if (state.priceLine) state.priceLine.applyOptions({ axisLabelVisible: true });
    } catch (_) {}
  }

  function dateForChartTime(time) {
    if (typeof time === 'number') return new Date(time * 1000);
    if (typeof time === 'string') return new Date(time);
    if (time && typeof time === 'object' && Number.isFinite(time.year)) {
      return new Date(Date.UTC(time.year, Number(time.month || 1) - 1, Number(time.day || 1)));
    }
    return new Date(NaN);
  }

  function formatDate(date, options) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
    const zone = timeMode === 'local' ? LOCAL_TIME_ZONE : 'UTC';
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone: zone }).format(date);
  }

  function formatTick(time, tickMarkType) {
    const date = dateForChartTime(time);
    if (tickMarkType === 0) return formatDate(date, { year: 'numeric' });
    if (tickMarkType === 1) return formatDate(date, { month: 'short' });
    if (tickMarkType === 2) return formatDate(date, { month: 'short', day: 'numeric' });
    if (tickMarkType === 4) return formatDate(date, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
    return formatDate(date, { hour: 'numeric', minute: '2-digit' });
  }

  function formatCrosshairTime(time) {
    const date = dateForChartTime(time);
    return formatDate(date, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
    });
  }

  function updateTimeButton() {
    const button = document.getElementById('chartTimeModeButton');
    if (!button) return;
    button.textContent = timeMode === 'local' ? 'LOCAL CT' : 'UTC';
    button.title = timeMode === 'local'
      ? 'Chart timestamps use Houston/Central time (America/Chicago). Click for UTC.'
      : 'Chart timestamps use UTC. Click for Houston/Central time.';
    button.setAttribute('aria-label', button.title);
  }

  function applyTimeMode() {
    if (!chartReady()) return;
    try {
      chart.applyOptions({
        localization: {
          locale: navigator.language || 'en-US',
          timeFormatter: formatCrosshairTime,
        },
      });
      chart.timeScale().applyOptions({ tickMarkFormatter: formatTick });
    } catch (error) {
      console.warn('Could not apply chart time mode.', error);
    }
    updateTimeButton();
  }

  function installTimeToggle() {
    const toolbar = document.querySelector('.chart-toolbar');
    if (!toolbar || document.getElementById('chartTimeModeButton')) return;
    const button = document.createElement('button');
    button.id = 'chartTimeModeButton';
    button.type = 'button';
    button.className = 'ghost-button chart-time-mode-button';
    button.addEventListener('click', () => {
      timeMode = timeMode === 'utc' ? 'local' : 'utc';
      localStorage.setItem(STORAGE_KEY, timeMode);
      applyTimeMode();
      window.dispatchEvent(new CustomEvent('traid-chart-time-mode', {
        detail: { mode: timeMode, timeZone: timeMode === 'local' ? LOCAL_TIME_ZONE : 'UTC' },
      }));
    });
    const refresh = document.getElementById('refreshForecast');
    toolbar.insertBefore(button, refresh || null);
    applyTimeMode();
  }

  function installFunctionGuards() {
    if (polishGuardsInstalled) return;
    polishGuardsInstalled = true;

    try {
      const baseUpdateLiveDirectionVisual = updateLiveDirectionVisual;
      updateLiveDirectionVisual = function polishedUpdateLiveDirectionVisual(...args) {
        const result = baseUpdateLiveDirectionVisual.apply(this, args);
        requestAnimationFrame(() => {
          positionClone();
          keepOnlyExplicitLivePriceLabel();
        });
        return result;
      };
    } catch (_) {}

    try {
      const baseApplyChartType = applyChartType;
      applyChartType = function polishedApplyChartType(...args) {
        const result = baseApplyChartType.apply(this, args);
        requestAnimationFrame(() => {
          positionClone();
          keepOnlyExplicitLivePriceLabel();
        });
        return result;
      };
    } catch (_) {}

    try {
      const baseSetCurrent = setCurrent;
      setCurrent = function polishedSetCurrent(row, ...args) {
        const result = baseSetCurrent.call(this, row, ...args);
        requestAnimationFrame(() => {
          if (!row) hideClone();
          else positionClone();
          keepOnlyExplicitLivePriceLabel();
        });
        return result;
      };
    } catch (_) {}
  }

  function initialize() {
    if (initialized) return;
    if (!chartReady()) {
      setTimeout(initialize, 60);
      return;
    }
    initialized = true;
    installStyles();
    ensureClone();
    installTimeToggle();
    installFunctionGuards();
    keepOnlyExplicitLivePriceLabel();
    requestAnimationFrame(positionClone);

    setInterval(keepOnlyExplicitLivePriceLabel, 1500);
  }

  setTimeout(initialize, 0);
})();