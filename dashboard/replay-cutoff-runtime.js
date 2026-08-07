(() => {
  const TIME_MODE_KEY = 'traid.chart.timeMode';
  const PREVIEW_LIMIT = 1000;
  let selectedCutoffISO = null;
  let pickerActive = false;
  let pickerTimes = [];
  let pickerMarketKey = '';
  let dragPointerId = null;
  let installed = false;

  const timeframeSeconds = Object.freeze({
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '4h': 14400,
    '1d': 86400,
  });

  function timeMode() {
    return localStorage.getItem(TIME_MODE_KEY) === 'local' ? 'local' : 'utc';
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function isoToInput(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    if (timeMode() === 'local') {
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  }

  function inputToISO(value) {
    if (!value) return null;
    const parsed = timeMode() === 'local'
      ? new Date(value)
      : new Date(`${value}:00Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }

  function displayTime(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString(undefined, timeMode() === 'utc'
      ? { timeZone: 'UTC', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function defaultCutoffISO() {
    const seconds = timeframeSeconds[state.timeframe] || 300;
    const horizon = Math.max(1, Number(document.getElementById('historicalReplayHorizon')?.value || 24));
    const barsBack = Math.max(120, horizon + 2);
    const timestamp = Math.floor((Date.now() / 1000 - barsBack * seconds) / seconds) * seconds;
    return new Date(timestamp * 1000).toISOString();
  }

  function updateZoneBadge() {
    const badge = document.getElementById('historicalReplayZoneBadge');
    if (badge) badge.textContent = timeMode() === 'local' ? 'LOCAL' : 'UTC';
  }

  function updateInputFromSelection() {
    const input = document.getElementById('historicalReplayDateTime');
    if (input && selectedCutoffISO) input.value = isoToInput(selectedCutoffISO);
    updateZoneBadge();
  }

  function installStyles() {
    if (document.getElementById('historicalReplayCutoffPickerStyles')) return;
    const style = document.createElement('style');
    style.id = 'historicalReplayCutoffPickerStyles';
    style.textContent = `
      .historical-replay-controls .legacy-candles-cutoff { display:none !important; }
      .historical-replay-time-field { position:relative; }
      .historical-replay-time-field .replay-zone-badge {
        position:absolute; right:9px; bottom:10px; pointer-events:none;
        color:#9fb0d8; font-size:7px; font-weight:850; letter-spacing:.08em;
      }
      #historicalReplayDateTime { padding-right:48px; }
      .historical-replay-pick-button.active {
        border-color:rgba(96,165,250,.55) !important;
        background:rgba(59,130,246,.14) !important;
        color:#dbeafe !important;
      }
      .historical-replay-cutoff-marker {
        position:absolute; z-index:8; top:0; bottom:0; width:14px;
        transform:translateX(-7px); cursor:ew-resize; touch-action:none;
        display:none; pointer-events:auto;
      }
      .historical-replay-cutoff-marker::before {
        content:''; position:absolute; left:6px; top:0; bottom:0; width:2px;
        background:linear-gradient(180deg,#60a5fa,#a855f7 52%,#d946ef);
        box-shadow:0 0 7px rgba(96,165,250,.58),0 0 13px rgba(168,85,247,.40);
      }
      .historical-replay-cutoff-marker.dragging::before {
        width:3px; left:5.5px; box-shadow:0 0 9px rgba(96,165,250,.82),0 0 18px rgba(168,85,247,.55);
      }
      .historical-replay-cutoff-label {
        position:absolute; top:8px; color:#f8fafc; font-size:8px; font-weight:850;
        letter-spacing:.08em; white-space:nowrap; pointer-events:none;
        text-shadow:0 1px 4px rgba(0,0,0,.8);
      }
      .historical-replay-cutoff-label.real { right:16px; }
      .historical-replay-cutoff-label.forecast { left:16px; }
      .historical-replay-pick-hint {
        position:absolute; z-index:7; right:10px; top:8px; display:none;
        padding:5px 7px; border:1px solid rgba(96,165,250,.22); border-radius:7px;
        background:rgba(7,11,27,.84); color:#aab6d4; font-size:8px; pointer-events:none;
      }
      .historical-replay-chart-wrap.cutoff-picking .historical-replay-pick-hint { display:block; }
      .historical-replay-chart-wrap.cutoff-picking { cursor:crosshair; }
      @media (max-width:700px) {
        .historical-replay-pick-button { grid-column:auto !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureMarker() {
    const wrap = document.querySelector('.historical-replay-chart-wrap');
    if (!wrap) return null;
    let marker = document.getElementById('historicalReplayCutoffMarker');
    if (!marker) {
      marker = document.createElement('div');
      marker.id = 'historicalReplayCutoffMarker';
      marker.className = 'historical-replay-cutoff-marker';
      marker.innerHTML = '<span class="historical-replay-cutoff-label real">REAL</span><span class="historical-replay-cutoff-label forecast">FORECAST</span>';
      wrap.appendChild(marker);
      marker.addEventListener('pointerdown', event => {
        if (!pickerActive) return;
        event.preventDefault();
        dragPointerId = event.pointerId;
        marker.classList.add('dragging');
        marker.setPointerCapture?.(event.pointerId);
        updateCutoffFromPointer(event);
      });
      marker.addEventListener('pointermove', event => {
        if (!pickerActive || dragPointerId !== event.pointerId) return;
        event.preventDefault();
        updateCutoffFromPointer(event);
      });
      const endDrag = event => {
        if (dragPointerId !== event.pointerId) return;
        dragPointerId = null;
        marker.classList.remove('dragging');
        try { marker.releasePointerCapture?.(event.pointerId); } catch (_) {}
      };
      marker.addEventListener('pointerup', endDrag);
      marker.addEventListener('pointercancel', endDrag);
    }
    if (!document.getElementById('historicalReplayPickHint')) {
      const hint = document.createElement('div');
      hint.id = 'historicalReplayPickHint';
      hint.className = 'historical-replay-pick-hint';
      hint.textContent = 'Click or drag the separator · snaps to completed candles';
      wrap.appendChild(hint);
    }
    return marker;
  }

  function unixSeconds(timestamp) {
    const value = Date.parse(timestamp);
    return Number.isFinite(value) ? Math.floor(value / 1000) : null;
  }

  function nearestPickerTime(seconds) {
    if (!pickerTimes.length || !Number.isFinite(seconds)) return null;
    let low = 0;
    let high = pickerTimes.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (pickerTimes[mid] < seconds) low = mid + 1;
      else high = mid;
    }
    const right = pickerTimes[low];
    const left = low > 0 ? pickerTimes[low - 1] : right;
    return Math.abs(seconds - left) <= Math.abs(right - seconds) ? left : right;
  }

  function positionMarker(iso = selectedCutoffISO) {
    const marker = ensureMarker();
    if (!marker || !historicalReplayChart || !iso) return;
    const raw = unixSeconds(iso);
    const snapped = nearestPickerTime(raw) ?? raw;
    if (!Number.isFinite(snapped)) return;
    let x = null;
    try { x = historicalReplayChart.timeScale().timeToCoordinate(snapped); } catch (_) {}
    if (!Number.isFinite(x)) {
      marker.style.display = 'none';
      return;
    }
    marker.style.left = `${x}px`;
    marker.style.display = 'block';
  }

  function selectCutoffSeconds(seconds, { updateInput = true } = {}) {
    const snapped = nearestPickerTime(seconds);
    if (!Number.isFinite(snapped)) return;
    selectedCutoffISO = new Date(snapped * 1000).toISOString();
    if (updateInput) updateInputFromSelection();
    positionMarker(selectedCutoffISO);
    const progress = document.getElementById('historicalReplayProgress');
    if (progress && pickerActive) progress.textContent = `Cutoff selected · ${displayTime(selectedCutoffISO)} · generate to test`;
  }

  function updateCutoffFromPointer(event) {
    if (!historicalReplayChart) return;
    const node = document.getElementById('historicalReplayChart');
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    let chartTime = null;
    try { chartTime = historicalReplayChart.timeScale().coordinateToTime(x); } catch (_) {}
    const seconds = typeof chartTime === 'number'
      ? chartTime
      : chartTime && Number.isFinite(chartTime.year)
        ? Date.UTC(chartTime.year, Number(chartTime.month || 1) - 1, Number(chartTime.day || 1)) / 1000
        : null;
    if (Number.isFinite(seconds)) selectCutoffSeconds(seconds);
  }

  function buildPreviewChart(candles) {
    historicalReplayPause();
    historicalReplayPayload = null;
    historicalReplayIndex = 0;
    historicalReplayDestroyChart();
    const node = document.getElementById('historicalReplayChart');
    if (!node) return;

    historicalReplayChart = LightweightCharts.createChart(node, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#070b1b' }, textColor: '#7f8aa8', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(139,158,213,.045)' }, horzLines: { color: 'rgba(139,158,213,.045)' } },
      rightPriceScale: { borderColor: 'rgba(139,158,213,.13)', scaleMargins: { top: .08, bottom: .10 } },
      timeScale: { borderColor: 'rgba(139,158,213,.13)', timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 6 },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });

    const spec = typeof symbolPriceSpec === 'function'
      ? symbolPriceSpec(state.symbol, state.quote)
      : { precision: 4, minMove: .0001 };
    historicalReplayHistorySeries = historicalReplayChart.addSeries(LightweightCharts.CandlestickSeries, {
      priceFormat: { type: 'price', precision: spec.precision, minMove: spec.minMove },
      upColor: '#2dd4bf', downColor: '#fb7185', borderVisible: false,
      wickUpColor: '#2dd4bf', wickDownColor: '#fb7185', priceLineVisible: false, lastValueVisible: false,
    });
    historicalReplayForecastSeries = null;
    historicalReplayForecastLine = null;
    historicalReplayActualSeries = null;

    const rows = historicalReplayRows(candles);
    historicalReplayHistorySeries.setData(rows.map(toCandle));
    pickerTimes = rows.map(row => unixSeconds(row.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
    historicalReplayChart.timeScale().fitContent();
    historicalReplayChart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(() => positionMarker()));

    if (selectedCutoffISO) {
      const snapped = nearestPickerTime(unixSeconds(selectedCutoffISO));
      if (Number.isFinite(snapped)) selectedCutoffISO = new Date(snapped * 1000).toISOString();
    } else if (pickerTimes.length) {
      selectedCutoffISO = new Date(pickerTimes[Math.max(0, pickerTimes.length - 120)] * 1000).toISOString();
    }
    updateInputFromSelection();
    requestAnimationFrame(() => positionMarker());
  }

  async function loadPickerPreview() {
    const key = `${state.symbol}:${state.timeframe}`;
    const progress = document.getElementById('historicalReplayProgress');
    if (progress) progress.textContent = `Loading ${state.symbol} ${state.timeframe} completed candles…`;
    const payload = await api(`/v1/candles/${encodeURIComponent(state.symbol)}?timeframe=${encodeURIComponent(state.timeframe)}&limit=${PREVIEW_LIMIT}`);
    pickerMarketKey = key;
    buildPreviewChart(payload.candles || []);
    if (progress) progress.textContent = `Drag the separator to choose the simulated present · ${displayTime(selectedCutoffISO)}`;
  }

  async function togglePicker() {
    pickerActive = !pickerActive;
    const button = document.getElementById('historicalReplayPick');
    const wrap = document.querySelector('.historical-replay-chart-wrap');
    button?.classList.toggle('active', pickerActive);
    if (button) button.textContent = pickerActive ? 'Done picking' : 'Pick on chart';
    wrap?.classList.toggle('cutoff-picking', pickerActive);
    if (!pickerActive) return;
    try {
      if (pickerMarketKey !== `${state.symbol}:${state.timeframe}` || !historicalReplayChart || !pickerTimes.length) {
        await loadPickerPreview();
      } else {
        positionMarker();
      }
    } catch (error) {
      pickerActive = false;
      button?.classList.remove('active');
      if (button) button.textContent = 'Pick on chart';
      wrap?.classList.remove('cutoff-picking');
      toast(error.message, 'error');
    }
  }

  async function generateAtExactTime() {
    const generate = document.getElementById('historicalReplayGenerate');
    const dateInput = document.getElementById('historicalReplayDateTime');
    const horizonInput = document.getElementById('historicalReplayHorizon');
    if (!generate || !dateInput || !horizonInput) return;

    const typedISO = inputToISO(dateInput.value);
    if (typedISO) selectedCutoffISO = typedISO;
    if (!selectedCutoffISO) {
      toast('Choose a replay date and time first.', 'error');
      return;
    }

    const horizon = Math.max(1, Math.min(200, Number(horizonInput.value) || 24));
    horizonInput.value = String(horizon);
    historicalReplayPause();
    historicalReplayPayload = null;
    historicalReplayIndex = 0;
    generate.disabled = true;
    generate.textContent = 'Running Kronos…';
    const progress = document.getElementById('historicalReplayProgress');
    if (progress) progress.textContent = `Freezing ${state.symbol} ${state.timeframe} at ${displayTime(selectedCutoffISO)}…`;
    ['historicalReplayPlay', 'historicalReplayStep', 'historicalReplayReset'].forEach(id => {
      const button = document.getElementById(id);
      if (button) button.disabled = true;
    });

    try {
      const payload = await api('/v1/replay/kronos', {
        method: 'POST',
        body: JSON.stringify({
          symbol: state.symbol,
          timeframe: state.timeframe,
          cutoff_timestamp: selectedCutoffISO,
          pred_len: horizon,
        }),
      });
      historicalReplayPayload = payload;
      historicalReplayIndex = 0;
      selectedCutoffISO = payload.cutoff_timestamp || selectedCutoffISO;
      updateInputFromSelection();
      historicalReplayBuildChart(payload);
      pickerTimes = [
        ...historicalReplayRows(payload.history),
        ...historicalReplayRows(payload.actual),
      ].map(row => unixSeconds(row.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
      historicalReplayChart?.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(() => positionMarker()));
      historicalReplayUpdateStats();
      requestAnimationFrame(() => positionMarker());
      document.getElementById('historicalReplayReset').disabled = false;
      const seconds = Number(payload.inference_ms || 0) / 1000;
      toast(`Historical Kronos forecast ready in ${seconds.toFixed(1)}s.`, 'success');
    } catch (error) {
      if (progress) progress.textContent = error.message;
      toast(error.message, 'error');
    } finally {
      generate.disabled = false;
      generate.textContent = 'Generate Kronos forecast';
    }
  }

  function installControls() {
    const controls = document.querySelector('.historical-replay-controls');
    const oldGenerate = document.getElementById('historicalReplayGenerate');
    if (!controls || !oldGenerate || document.getElementById('historicalReplayDateTime')) return false;

    const legacyCutoff = document.getElementById('historicalReplayCutoff')?.closest('label');
    legacyCutoff?.classList.add('legacy-candles-cutoff');

    const timeLabel = document.createElement('label');
    timeLabel.className = 'historical-replay-time-field';
    timeLabel.innerHTML = 'Rewind to · date & time<input id="historicalReplayDateTime" type="datetime-local" step="60" /><span id="historicalReplayZoneBadge" class="replay-zone-badge">UTC</span>';
    controls.insertBefore(timeLabel, controls.firstElementChild);

    const pick = document.createElement('button');
    pick.id = 'historicalReplayPick';
    pick.type = 'button';
    pick.className = 'ghost-button historical-replay-pick-button';
    pick.textContent = 'Pick on chart';
    controls.insertBefore(pick, oldGenerate);

    // Replace the button to remove the legacy candles-ago click listener.
    const generate = oldGenerate.cloneNode(true);
    oldGenerate.replaceWith(generate);
    generate.addEventListener('click', generateAtExactTime);
    pick.addEventListener('click', togglePicker);

    selectedCutoffISO = defaultCutoffISO();
    updateInputFromSelection();
    document.getElementById('historicalReplayDateTime')?.addEventListener('change', event => {
      const iso = inputToISO(event.target.value);
      if (!iso) return;
      selectedCutoffISO = iso;
      positionMarker();
      const progress = document.getElementById('historicalReplayProgress');
      if (progress) progress.textContent = `Cutoff selected · ${displayTime(selectedCutoffISO)} · generate to test`;
    });

    const wrap = document.querySelector('.historical-replay-chart-wrap');
    wrap?.addEventListener('pointerdown', event => {
      if (!pickerActive || event.target.closest('#historicalReplayCutoffMarker')) return;
      updateCutoffFromPointer(event);
    });
    ensureMarker();
    return true;
  }

  function handleTimeModeChange() {
    if (!selectedCutoffISO) return;
    updateInputFromSelection();
  }

  function initialize() {
    if (installed) return;
    if (typeof historicalReplayBuildChart !== 'function' || !document.getElementById('historicalReplayGenerate')) {
      setTimeout(initialize, 70);
      return;
    }
    installStyles();
    if (!installControls()) {
      setTimeout(initialize, 70);
      return;
    }
    installed = true;
    window.addEventListener('traid-chart-time-mode', handleTimeModeChange);
  }

  setTimeout(initialize, 0);
})();
