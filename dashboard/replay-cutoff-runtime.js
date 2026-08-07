(() => {
  const TIME_MODE_KEY = 'traid.chart.timeMode';
  const LOCAL_TIME_ZONE = 'America/Chicago';
  const PREVIEW_LIMIT = 1000;
  let selectedCutoffISO = null;
  let pickerActive = false;
  let pickerTimes = [];
  let pickerMarketKey = '';
  let dragPointerId = null;
  let installed = false;
  let replayStatsPatched = false;

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

  function effectiveZone() {
    return timeMode() === 'local' ? LOCAL_TIME_ZONE : 'UTC';
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function zoneParts(date, timeZone = effectiveZone()) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(date);
    const values = {};
    for (const part of parts) {
      if (part.type !== 'literal') values[part.type] = Number(part.value);
    }
    return values;
  }

  function isoToInput(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const parts = zoneParts(date);
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
  }

  function wallTimeToISO(value, timeZone) {
    const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return null;
    const [, year, month, day, hour, minute] = match.map(Number);
    const targetWallClockAsUTC = Date.UTC(year, month - 1, day, hour, minute, 0, 0);
    let guess = targetWallClockAsUTC;

    // Convert an IANA-zone wall clock to an instant without relying on the
    // browser/OS timezone. A second pass handles DST offset changes cleanly.
    for (let pass = 0; pass < 3; pass += 1) {
      const represented = zoneParts(new Date(guess), timeZone);
      const representedAsUTC = Date.UTC(
        represented.year,
        represented.month - 1,
        represented.day,
        represented.hour,
        represented.minute,
        represented.second || 0,
      );
      const delta = representedAsUTC - targetWallClockAsUTC;
      if (Math.abs(delta) < 1000) break;
      guess -= delta;
    }
    const parsed = new Date(guess);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }

  function inputToISO(value) {
    if (!value) return null;
    if (timeMode() === 'local') return wallTimeToISO(value, LOCAL_TIME_ZONE);
    const parsed = new Date(`${value}:00Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }

  function displayTime(iso, includeYear = false) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat(undefined, {
      timeZone: effectiveZone(),
      month: 'short',
      day: 'numeric',
      ...(includeYear ? { year: 'numeric' } : {}),
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(date);
  }

  function defaultCutoffISO() {
    const seconds = timeframeSeconds[state.timeframe] || 300;
    const horizon = Math.max(1, Number(document.getElementById('historicalReplayHorizon')?.value || 24));
    const barsBack = Math.max(24, horizon + 1);
    const timestamp = Math.floor((Date.now() / 1000 - barsBack * seconds) / seconds) * seconds;
    return new Date(timestamp * 1000).toISOString();
  }

  function setProgress(message, tone = '') {
    const progress = document.getElementById('historicalReplayProgress');
    if (!progress) return;
    progress.textContent = message;
    progress.classList.remove('error', 'success', 'working');
    if (tone) progress.classList.add(tone);
  }

  function updateZoneBadge() {
    const badge = document.getElementById('historicalReplayZoneBadge');
    if (!badge) return;
    badge.textContent = timeMode() === 'local' ? 'CT' : 'UTC';
    badge.title = timeMode() === 'local'
      ? 'Houston local time · America/Chicago'
      : 'Coordinated Universal Time';
  }

  function updateInputFromSelection() {
    const input = document.getElementById('historicalReplayDateTime');
    if (input && selectedCutoffISO) {
      input.value = isoToInput(selectedCutoffISO);
      input.max = isoToInput(new Date().toISOString());
    }
    updateZoneBadge();
  }

  function dateForChartTime(time) {
    if (typeof time === 'number') return new Date(time * 1000);
    if (typeof time === 'string') return new Date(time);
    if (time && typeof time === 'object' && Number.isFinite(time.year)) {
      return new Date(Date.UTC(time.year, Number(time.month || 1) - 1, Number(time.day || 1)));
    }
    return new Date(NaN);
  }

  function replayTickFormatter(time, tickMarkType) {
    const date = dateForChartTime(time);
    if (Number.isNaN(date.getTime())) return '';
    const options = { timeZone: effectiveZone() };
    if (tickMarkType === 0) options.year = 'numeric';
    else if (tickMarkType === 1) options.month = 'short';
    else if (tickMarkType === 2) { options.month = 'short'; options.day = 'numeric'; }
    else { options.hour = 'numeric'; options.minute = '2-digit'; }
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }

  function replayCrosshairFormatter(time) {
    const date = dateForChartTime(time);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, {
      timeZone: effectiveZone(),
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
    }).format(date);
  }

  function applyReplayChartTimeZone() {
    if (!historicalReplayChart) return;
    try {
      historicalReplayChart.applyOptions({
        localization: {
          locale: navigator.language || 'en-US',
          timeFormatter: replayCrosshairFormatter,
        },
      });
      historicalReplayChart.timeScale().applyOptions({ tickMarkFormatter: replayTickFormatter });
    } catch (_) {}
  }

  function installStyles() {
    if (document.getElementById('historicalReplayCutoffPickerStyles')) return;
    const style = document.createElement('style');
    style.id = 'historicalReplayCutoffPickerStyles';
    style.textContent = `
      #analysisTabs button { font-size:11px !important; }
      .tab-content[data-content="replay"] { padding:18px 18px 22px !important; }
      .historical-replay-shell { gap:15px !important; }
      .historical-replay-copy {
        padding:2px 2px 0;
        align-items:center !important;
      }
      .historical-replay-copy h3 {
        font-size:18px !important;
        letter-spacing:-.02em;
      }
      .historical-replay-copy p {
        margin-top:6px !important;
        max-width:920px !important;
        font-size:12px !important;
        line-height:1.55 !important;
        color:#99a5c5 !important;
      }
      .historical-replay-controls {
        display:grid !important;
        grid-template-columns:minmax(280px,1.45fr) minmax(140px,.45fr) auto auto !important;
        gap:10px !important;
        align-items:end !important;
        padding:14px !important;
        border:1px solid rgba(139,158,213,.15);
        border-radius:13px;
        background:linear-gradient(135deg,rgba(13,20,43,.78),rgba(9,13,31,.62));
      }
      .historical-replay-controls .legacy-candles-cutoff { display:none !important; }
      .historical-replay-controls label,
      .historical-replay-speed {
        display:grid;
        gap:7px !important;
        color:#a8b3d2 !important;
        font-size:11px !important;
        font-weight:750 !important;
        letter-spacing:.015em !important;
      }
      .historical-replay-controls input,
      .historical-replay-speed select {
        width:100%;
        height:44px !important;
        font-size:13px !important;
        font-weight:650;
      }
      .historical-replay-controls .primary-button,
      .historical-replay-pick-button {
        min-height:44px !important;
        padding:0 17px !important;
        font-size:12px !important;
        white-space:nowrap;
      }
      .historical-replay-time-field { position:relative; }
      .historical-replay-time-field .replay-zone-badge {
        position:absolute; right:12px; bottom:14px; pointer-events:none;
        color:#bfdbfe; font-size:9px; font-weight:900; letter-spacing:.08em;
      }
      #historicalReplayDateTime { padding-right:52px !important; }
      .historical-replay-pick-button.active {
        border-color:rgba(96,165,250,.55) !important;
        background:rgba(59,130,246,.14) !important;
        color:#dbeafe !important;
      }
      .historical-replay-transport {
        display:flex !important;
        align-items:end !important;
        flex-wrap:wrap;
        gap:9px !important;
        padding:12px 14px !important;
        border:1px solid rgba(139,158,213,.13) !important;
        border-radius:12px;
        background:rgba(8,12,29,.55);
      }
      .historical-replay-transport .ghost-button {
        min-width:96px !important;
        min-height:40px !important;
        font-size:11px !important;
      }
      .historical-replay-speed { width:120px !important; }
      .historical-replay-progress {
        margin-left:auto !important;
        align-self:center !important;
        max-width:min(680px,48vw);
        padding:7px 10px;
        border-radius:8px;
        color:#aeb9d6 !important;
        font-size:11px !important;
        line-height:1.4;
        font-variant-numeric:tabular-nums;
      }
      .historical-replay-progress.error {
        color:#fecdd3 !important;
        background:rgba(190,24,93,.10);
        border:1px solid rgba(251,113,133,.20);
      }
      .historical-replay-progress.success {
        color:#bbf7d0 !important;
        background:rgba(16,185,129,.08);
        border:1px solid rgba(45,212,191,.17);
      }
      .historical-replay-progress.working {
        color:#bfdbfe !important;
        background:rgba(59,130,246,.08);
        border:1px solid rgba(96,165,250,.16);
      }
      .replay-stats { gap:10px !important; }
      .replay-stats article {
        padding:14px 16px !important;
        min-height:76px;
        border-radius:12px !important;
        background:rgba(8,12,29,.52) !important;
      }
      .replay-stats span { font-size:9px !important; }
      .replay-stats strong {
        margin-top:7px !important;
        font-size:16px !important;
        font-variant-numeric:tabular-nums;
      }
      .historical-replay-chart-wrap {
        height:350px !important;
        min-height:280px !important;
        border-radius:13px !important;
        border-color:rgba(139,158,213,.16) !important;
      }
      .historical-replay-legend {
        left:12px !important;
        top:10px !important;
        gap:12px !important;
        padding:7px 9px !important;
        border-radius:8px !important;
        font-size:10px !important;
      }
      .historical-replay-note {
        padding:10px 12px !important;
        border-radius:10px !important;
        font-size:10px !important;
        line-height:1.55 !important;
      }
      .historical-replay-cutoff-marker {
        position:absolute; z-index:8; top:0; bottom:0; width:18px;
        transform:translateX(-9px); cursor:ew-resize; touch-action:none;
        display:none; pointer-events:auto;
      }
      .historical-replay-cutoff-marker::before {
        content:''; position:absolute; left:8px; top:0; bottom:0; width:2px;
        background:linear-gradient(180deg,#60a5fa,#8b5cf6 52%,#d946ef);
        box-shadow:0 0 8px rgba(96,165,250,.62),0 0 16px rgba(139,92,246,.43);
      }
      .historical-replay-cutoff-marker.dragging::before {
        width:3px; left:7.5px; box-shadow:0 0 10px rgba(96,165,250,.86),0 0 20px rgba(139,92,246,.58);
      }
      .historical-replay-cutoff-label {
        position:absolute; top:12px; color:#f8fafc; font-size:10px; font-weight:900;
        letter-spacing:.09em; white-space:nowrap; pointer-events:none;
        text-shadow:0 1px 5px rgba(0,0,0,.9);
      }
      .historical-replay-cutoff-label.real { right:20px; }
      .historical-replay-cutoff-label.forecast { left:20px; }
      .historical-replay-pick-hint {
        position:absolute; z-index:7; right:12px; top:10px; display:none;
        padding:7px 9px; border:1px solid rgba(96,165,250,.22); border-radius:8px;
        background:rgba(7,11,27,.88); color:#c3cee8; font-size:10px; pointer-events:none;
      }
      .historical-replay-chart-wrap.cutoff-picking .historical-replay-pick-hint { display:block; }
      .historical-replay-chart-wrap.cutoff-picking { cursor:crosshair; }
      @media (max-width:1050px) {
        .historical-replay-controls {
          grid-template-columns:minmax(240px,1fr) minmax(120px,.45fr) auto !important;
        }
        .historical-replay-controls .primary-button { grid-column:1/-1; }
        .historical-replay-progress { width:100%; max-width:none; margin-left:0 !important; }
      }
      @media (max-width:700px) {
        .tab-content[data-content="replay"] { padding:12px !important; }
        .historical-replay-controls { grid-template-columns:1fr 1fr !important; }
        .historical-replay-controls .primary-button { grid-column:1/-1; }
        .historical-replay-pick-button { grid-column:auto !important; }
        .historical-replay-chart-wrap { height:300px !important; }
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
    if (pickerActive) setProgress(`Cutoff selected · ${displayTime(selectedCutoffISO)} · generate to test`);
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
      layout: { background: { type: 'solid', color: '#070b1b' }, textColor: '#8f9bb9', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(139,158,213,.045)' }, horzLines: { color: 'rgba(139,158,213,.045)' } },
      rightPriceScale: { borderColor: 'rgba(139,158,213,.13)', scaleMargins: { top: .08, bottom: .10 } },
      timeScale: { borderColor: 'rgba(139,158,213,.13)', timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 6, tickMarkFormatter: replayTickFormatter },
      localization: { locale: navigator.language || 'en-US', timeFormatter: replayCrosshairFormatter },
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
    setProgress(`Loading ${state.symbol} ${state.timeframe} completed candles…`, 'working');
    const payload = await api(`/v1/candles/${encodeURIComponent(state.symbol)}?timeframe=${encodeURIComponent(state.timeframe)}&limit=${PREVIEW_LIMIT}`);
    pickerMarketKey = key;
    buildPreviewChart(payload.candles || []);
    setProgress(`Drag the separator to choose the simulated present · ${displayTime(selectedCutoffISO)}`);
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
      setProgress(error.message, 'error');
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
    if (Date.parse(selectedCutoffISO) > Date.now() + 60000) {
      setProgress('Replay cutoff must be in the past.', 'error');
      toast('Replay cutoff must be in the past.', 'error');
      return;
    }

    const horizon = Math.max(1, Math.min(200, Number(horizonInput.value) || 24));
    horizonInput.value = String(horizon);
    historicalReplayPause();
    historicalReplayPayload = null;
    historicalReplayIndex = 0;
    generate.disabled = true;
    generate.textContent = 'Running Kronos…';
    setProgress(`Freezing ${state.symbol} ${state.timeframe} at ${displayTime(selectedCutoffISO)}…`, 'working');
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
      applyReplayChartTimeZone();
      pickerTimes = [
        ...historicalReplayRows(payload.history),
        ...historicalReplayRows(payload.actual),
      ].map(row => unixSeconds(row.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
      historicalReplayChart?.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(() => positionMarker()));
      historicalReplayUpdateStats();
      requestAnimationFrame(() => positionMarker());
      document.getElementById('historicalReplayReset').disabled = false;
      const seconds = Number(payload.inference_ms || 0) / 1000;
      const actualCount = Number(payload.available_actual_candles ?? payload.actual?.length ?? 0);
      const projectionCount = Number(payload.projection_candles ?? horizon);
      setProgress(
        `${actualCount}/${projectionCount} realized candles currently available · forecast frozen at ${displayTime(payload.cutoff_timestamp)}`,
        'success',
      );
      toast(`Historical Kronos forecast ready in ${seconds.toFixed(1)}s.`, 'success');
    } catch (error) {
      setProgress(error.message, 'error');
      toast(error.message, 'error');
    } finally {
      generate.disabled = false;
      generate.textContent = 'Generate Kronos forecast';
    }
  }

  function installStatsTimePatch() {
    if (replayStatsPatched || typeof historicalReplayUpdateStats !== 'function') return;
    replayStatsPatched = true;
    const base = historicalReplayUpdateStats;
    historicalReplayUpdateStats = function patchedHistoricalReplayUpdateStats(...args) {
      const result = base.apply(this, args);
      const payload = historicalReplayPayload;
      if (!payload) return result;

      const cutoffStat = document.getElementById('historicalReplayCutoffStat');
      if (cutoffStat) cutoffStat.textContent = displayTime(payload.cutoff_timestamp, true);

      const actual = historicalReplayRows(payload.actual);
      const progress = document.getElementById('historicalReplayProgress');
      if (progress && historicalReplayIndex === 0 && !progress.classList.contains('working') && !progress.classList.contains('error')) {
        progress.textContent = `${actual.length}/${payload.projection_candles || payload.projection?.length || 0} realized candles available · forecast frozen at ${displayTime(payload.cutoff_timestamp)}`;
      }
      return result;
    };
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
      setProgress(`Cutoff selected · ${displayTime(selectedCutoffISO)} · generate to test`);
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
    applyReplayChartTimeZone();
    historicalReplayUpdateStats?.();
  }

  function initialize() {
    if (installed) return;
    if (typeof historicalReplayBuildChart !== 'function' || !document.getElementById('historicalReplayGenerate')) {
      setTimeout(initialize, 70);
      return;
    }
    installStyles();
    installStatsTimePatch();
    if (!installControls()) {
      setTimeout(initialize, 70);
      return;
    }
    installed = true;
    window.addEventListener('traid-chart-time-mode', handleTimeModeChange);
  }

  setTimeout(initialize, 0);
})();
