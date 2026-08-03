(() => {
  const scriptUrl = new URL('./app.js', document.currentScript.src);

  const fail = error => {
    console.error('Traid dashboard loader failed', error);
    const node = document.createElement('div');
    node.style.cssText = 'position:fixed;inset:16px;z-index:99999;padding:16px;border:1px solid #fb7185;background:#170b18;color:#fecdd3;font:14px/1.5 system-ui;border-radius:12px;white-space:pre-wrap';
    node.textContent = `Dashboard startup failed: ${error.message}`;
    document.body.appendChild(node);
  };

  const replaceBlock = (source, startMarker, endMarker, replacement) => {
    const start = source.indexOf(startMarker);
    if (start < 0) throw new Error(`Patch marker was not found: ${startMarker}`);
    const end = source.indexOf(endMarker, start);
    if (end < 0) throw new Error(`Patch end marker was not found: ${endMarker}`);
    return `${source.slice(0, start)}${replacement}${source.slice(end)}`;
  };

  fetch(scriptUrl, { cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error(`Could not load app.js (${response.status})`);
      return response.text();
    })
    .then(original => {
      let source = original;

      // Repair the historical forecast-volume parenthesis typo when an older checkout still has it.
      source = source.replace(
        "forecastVolume.setData(rows.map(row => toVolume(row, 'rgba(139,92,246,.32)'));",
        "forecastVolume.setData(rows.map(row => toVolume(row, 'rgba(139,92,246,.32)')));"
      );

      source = source.replace(
        "firstClose: null, lastQuoteAt: 0, priceLine: null,",
        "firstClose: null, lastQuoteAt: 0, lastStreamAt: 0, priceLine: null,\n  marketRequestId: 0, lastCompletedCandleTime: 0, currentCandleTime: 0, lastStreamErrorAt: 0, lastStreamErrorDetail: '',"
      );

      source = source.replace(
        "const ease = progress => progress * progress * (3 - 2 * progress);",
        `const ease = progress => progress * progress * (3 - 2 * progress);
const TIMEFRAME_SECONDS = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };
const sameTimestamp = (first, second) => Number.isFinite(toTime(first)) && toTime(first) === toTime(second);

function rowsMatchTimeframe(rows, timeframe = state.timeframe) {
  const expected = TIMEFRAME_SECONDS[timeframe];
  if (!expected || !Array.isArray(rows) || rows.length < 2) return true;
  const times = [...new Set(rows.map(row => toTime(row.timestamp)).filter(Number.isFinite))].sort((a, b) => a - b);
  const diffs = [];
  for (let index = 1; index < times.length; index += 1) {
    const difference = times[index] - times[index - 1];
    if (difference > 0 && difference <= expected * 2.5) diffs.push(difference);
  }
  if (!diffs.length) return false;
  diffs.sort((a, b) => a - b);
  const median = diffs[Math.floor(diffs.length / 2)];
  return Math.abs(median - expected) <= Math.max(1, expected * 0.1);
}

function visibleForecastRows(rows) {
  if (!rowsMatchTimeframe(rows)) return [];
  if (!state.lastCompletedCandleTime) return rows;
  return rows.filter(row => toTime(row.timestamp) > state.lastCompletedCandleTime);
}

function currentMarketRequest(requestId, symbol, timeframe) {
  return requestId === state.marketRequestId && symbol === state.symbol && timeframe === state.timeframe;
}`
      );

      source = replaceBlock(
        source,
        'function setHistory(rows) {',
        '\n\nfunction setCurrent(row) {',
        `function setHistory(rows) {
  if (!rowsMatchTimeframe(rows)) {
    console.warn('Ignored market history with the wrong candle cadence.', { timeframe: state.timeframe, rows });
    return false;
  }
  marketCandles.setData(rows.map(toCandle)); marketLine.setData(rows.map(toLine));
  marketVolume.setData(rows.map(row => toVolume(row, +row.close >= +row.open ? 'rgba(45,212,191,.23)' : 'rgba(251,113,133,.21)')));
  state.firstClose = rows.length ? +rows[0].close : null;
  state.lastCompletedCandleTime = rows.length ? toTime(rows[rows.length - 1].timestamp) : 0;
  if (state.currentCandleTime <= state.lastCompletedCandleTime) state.currentCandleTime = 0;
  return true;
}`
      );

      source = replaceBlock(
        source,
        'function setCurrent(row) {',
        '\n\nfunction updateQuote(quote) {',
        `function setCurrent(row, { allowReset = false } = {}) {
  if (!row) {
    liveCandles.setData([]); liveLine.setData([]); state.currentCandleTime = 0; return false;
  }
  const candleTime = toTime(row.timestamp);
  if (!Number.isFinite(candleTime)) return false;
  if (!allowReset && state.currentCandleTime && candleTime < state.currentCandleTime) {
    console.warn('Ignored an out-of-order live candle.', { candleTime, currentCandleTime: state.currentCandleTime });
    return false;
  }
  if (state.lastCompletedCandleTime && candleTime <= state.lastCompletedCandleTime) {
    return false;
  }
  state.currentCandleTime = candleTime;
  liveCandles.setData([toCandle(row)]); liveLine.setData([toLine(row)]);
  marketVolume.update(toVolume(row, +row.close >= +row.open ? 'rgba(34,211,238,.24)' : 'rgba(56,189,248,.22)'));
  return true;
}`
      );

      source = replaceBlock(
        source,
        'function renderPriorForecasts() {',
        '\n\nfunction alignForecast(fromRows, toRows) {',
        `function renderPriorForecasts() {
  const previous = visibleForecastRows(state.projectionHistory[1] || []);
  const older = visibleForecastRows(state.projectionHistory[2] || []);
  previousCandles.setData(previous.map(toCandle)); previousLine.setData(previous.map(toLine));
  olderCandles.setData(older.map(toCandle)); olderLine.setData(older.map(toLine));
  previousVolume.setData(previous.map(row => toVolume(row, 'rgba(132,128,165,.21)')));
  olderVolume.setData(older.map(row => toVolume(row, 'rgba(145,145,155,.105)')));
}`
      );

      source = replaceBlock(
        source,
        'function setUncertainty(uncertainty) {',
        '\n\nfunction setProjection(rows, uncertainty = null, revision = null, generatedAt = null) {',
        `function setUncertainty(uncertainty) {
  if (!state.settings.advanced && !$('advancedForecast').checked || !uncertainty) {
    p10Line.setData([]); p90Line.setData([]); return;
  }
  p10Line.setData(visibleForecastRows(uncertainty.p10 || []).map(toLine));
  p90Line.setData(visibleForecastRows(uncertainty.p90 || []).map(toLine));
}`
      );

      source = replaceBlock(
        source,
        'function setProjection(rows, uncertainty = null, revision = null, generatedAt = null) {',
        '\n\nasync function primeProjectionHistory(currentId) {',
        `function setProjection(rows, uncertainty = null, revision = null, generatedAt = null) {
  if (!rowsMatchTimeframe(rows)) {
    console.warn('Ignored a forecast with the wrong candle cadence.', { timeframe: state.timeframe, rows });
    return false;
  }
  const next = cloneProjection(visibleForecastRows(rows));
  if (!next.length) return false;
  const priorActive = state.projectionHistory[0] || [];
  const sameAsActive = priorActive.length === next.length && priorActive.every((row, index) => sameTimestamp(row.timestamp, next[index]?.timestamp) && +row.close === +next[index]?.close);
  const animationStart = state.renderedProjection.length ? cloneProjection(state.renderedProjection) : cloneProjection(priorActive);
  const priorPrevious = state.projectionHistory[1] || [];
  state.projectionHistory = sameAsActive
    ? [next, priorPrevious, state.projectionHistory[2] || []].filter(item => item.length).slice(0, 3)
    : [next, priorActive, priorPrevious].filter(item => item.length).slice(0, 3);
  renderPriorForecasts(); animateActiveForecast(animationStart, next); setUncertainty(uncertainty);
  $('forecastStatus').textContent = generatedAt ? \`Updated \${new Date(generatedAt).toLocaleTimeString()}\` : \`Active \${rows.length} candles\`;
  $('forecastPill').textContent = \`FORECAST \${rows.length}\`;
  renderRevision(revision);
  return true;
}`
      );

      source = replaceBlock(
        source,
        'async function primeProjectionHistory(currentId) {',
        '\n\nfunction renderRevision(revision) {',
        `async function primeProjectionHistory(currentId, symbol = state.symbol, timeframe = state.timeframe, requestId = state.marketRequestId, inputLastTimestamp = null) {
  try {
    const payload = await api(\`/v1/forecasts/\${symbol}?timeframe=\${timeframe}&limit=12\`);
    if (!currentMarketRequest(requestId, symbol, timeframe)) return;
    const history = payload.forecasts
      .filter(item => item.id !== currentId)
      .filter(item => !inputLastTimestamp || !sameTimestamp(item.input_last_timestamp, inputLastTimestamp))
      .map(item => item.projection)
      .filter(rows => rowsMatchTimeframe(rows, timeframe))
      .slice(0, 2);
    state.projectionHistory = [state.projectionHistory[0] || [], ...history].filter(item => item.length).slice(0, 3);
    renderPriorForecasts(); renderAccuracy(payload.accuracy);
  } catch (error) {
    if (currentMarketRequest(requestId, symbol, timeframe)) console.warn(error);
  }
}`
      );

      source = replaceBlock(
        source,
        'async function loadMarket({ fit = true } = {}) {',
        '\n\nfunction connectStream() {',
        `async function loadMarket({ fit = true } = {}) {
  const requestId = ++state.marketRequestId;
  const requestedSymbol = state.symbol;
  const requestedTimeframe = state.timeframe;
  const advanced = $('advancedForecast').checked;
  const predLen = Math.max(1, +$('predLen').value || 24);
  const uncertaintyPaths = advanced ? Math.max(3, +$('uncertaintyPaths').value || 7) : null;

  if (state.socket) {
    const priorSocket = state.socket;
    state.socket = null;
    priorSocket.close();
  }
  state.lastCompletedCandleTime = 0;
  state.currentCandleTime = 0;
  state.lastStreamAt = 0;
  resetProjectionHistory(); setFeed(false, 'Loading market and model…');
  $('marketName').textContent = names[requestedSymbol]; $('symbolSelect').value = requestedSymbol;
  $('watermarkMarket').textContent = \`\${requestedSymbol} · \${requestedTimeframe}\`;
  $('forecastStatus').textContent = advanced ? 'Loading advanced forecast…' : 'Loading forecast…';
  document.querySelectorAll('.watch-item').forEach(item => item.classList.toggle('active', item.dataset.symbol === requestedSymbol));

  try {
    const [candlesPayload, forecastHistory, health] = await Promise.all([
      api(\`/v1/candles/\${requestedSymbol}?timeframe=\${requestedTimeframe}&limit=2\`),
      api(\`/v1/forecasts/\${requestedSymbol}?timeframe=\${requestedTimeframe}&limit=25\`),
      api('/health'),
    ]);
    if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;

    const inputLastTimestamp = candlesPayload.candles.at(-1)?.timestamp || null;
    const cached = forecastHistory.forecasts.find(item => {
      const parameters = item.parameters || {};
      const sameInput = inputLastTimestamp && sameTimestamp(item.input_last_timestamp, inputLastTimestamp);
      const sameModel = !health.model || item.model_id === health.model;
      const sameParameters = +parameters.lookback === 400 && +parameters.pred_len === predLen && +parameters.sample_count === 5;
      const sameMode = advanced ? Boolean(item.uncertainty) && +item.uncertainty.paths === uncertaintyPaths : !item.uncertainty;
      return sameInput && sameModel && sameParameters && sameMode && rowsMatchTimeframe(item.projection, requestedTimeframe);
    });

    let payload;
    if (cached) {
      const quotePayload = await api(\`/v1/quote/\${requestedSymbol}?timeframe=\${requestedTimeframe}\`);
      if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;
      payload = {
        ...cached,
        provider: quotePayload.provider,
        model: health.model,
        quote: quotePayload.quote,
        current_candle: quotePayload.current_candle,
        accuracy: forecastHistory.accuracy,
        reused: true,
      };
    } else {
      payload = await api('/v1/forecast', {
        method: 'POST',
        body: JSON.stringify({
          symbol: requestedSymbol,
          timeframe: requestedTimeframe,
          lookback: 400,
          pred_len: predLen,
          sample_count: 5,
          advanced,
          uncertainty_paths: uncertaintyPaths,
        }),
      });
    }

    if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;
    state.currentForecastId = payload.id;
    if (!setHistory(payload.history)) throw new Error('The returned market history does not match the selected timeframe.');
    setCurrent(payload.current_candle, { allowReset: true });
    state.projectionHistory = []; state.renderedProjection = [];
    if (!setProjection(payload.projection, payload.uncertainty, payload.revision, payload.generated_at)) {
      throw new Error('The returned forecast does not match the selected timeframe.');
    }
    updateQuote(payload.quote); renderAccuracy(payload.accuracy);
    await primeProjectionHistory(payload.id, requestedSymbol, requestedTimeframe, requestId, payload.input_last_timestamp || inputLastTimestamp);
    if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;
    await loadContext(requestedSymbol, requestedTimeframe, requestId);
    if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;
    if (fit) chart.timeScale().fitContent();
    connectStream(requestedSymbol, requestedTimeframe, requestId);
    setFeed(true, (payload.provider || 'market feed') + (payload.reused ? ' · cached forecast' : ' · live forecast'));
  } catch (error) {
    if (!currentMarketRequest(requestId, requestedSymbol, requestedTimeframe)) return;
    setFeed(false, error.message); toast(error.message, 'error');
  }
}`
      );

      source = replaceBlock(
        source,
        'function connectStream() {',
        '\n\nasync function loadContext() {',
        `function connectStream(symbol = state.symbol, timeframe = state.timeframe, requestId = state.marketRequestId) {
  const url = \`\${wsBase()}/v1/stream/\${symbol}?timeframe=\${timeframe}&with_forecast=true&advanced=\${$('advancedForecast').checked}&pred_len=\${Math.max(1,+$('predLen').value||24)}\`;
  const socket = new WebSocket(url); state.socket = socket;
  socket.onopen = () => {
    if (socket !== state.socket || !currentMarketRequest(requestId, symbol, timeframe)) return;
    state.lastStreamAt = Date.now(); setFeed(true);
  };
  socket.onmessage = event => {
    if (socket !== state.socket || !currentMarketRequest(requestId, symbol, timeframe)) return;
    state.lastStreamAt = Date.now();
    const message = JSON.parse(event.data);
    if (message.type === 'market_update') {
      updateQuote(message.quote);
      if (message.completed_candle) {
        const completedTime = toTime(message.completed_candle.timestamp);
        if (completedTime > state.lastCompletedCandleTime) {
          marketCandles.update(toCandle(message.completed_candle)); marketLine.update(toLine(message.completed_candle));
          marketVolume.update(toVolume(message.completed_candle, +message.completed_candle.close >= +message.completed_candle.open ? 'rgba(45,212,191,.23)' : 'rgba(251,113,133,.21)'));
          state.lastCompletedCandleTime = completedTime;
          if (state.currentCandleTime <= completedTime) {
            state.currentCandleTime = 0; liveCandles.setData([]); liveLine.setData([]);
          }
          renderPriorForecasts();
        }
      }
      setCurrent(message.current_candle);
      if (message.forecast_status) $('forecastStatus').textContent = message.forecast_status === 'queued' ? 'Forecast refresh queued…' : 'Refreshing forecast…';
    } else if (message.type === 'projection_update') {
      if (!rowsMatchTimeframe(message.projection, timeframe)) {
        console.warn('Ignored a projection update for a different timeframe.', message);
        return;
      }
      state.currentForecastId = message.id;
      setProjection(message.projection, message.uncertainty, message.revision, message.generated_at);
      renderAccuracy(message.accuracy); loadContext(symbol, timeframe, requestId);
    } else if (message.type === 'forecast_status') {
      $('forecastStatus').textContent = 'Refreshing queued forecast…';
    } else if (message.type === 'forecast_error' || message.type === 'error') {
      const detail = message.detail || 'Market stream error';
      if (detail !== state.lastStreamErrorDetail || Date.now() - state.lastStreamErrorAt > 10000) {
        state.lastStreamErrorDetail = detail; state.lastStreamErrorAt = Date.now(); toast(detail, 'error');
      }
    }
  };
  socket.onclose = () => {
    if (socket === state.socket && currentMarketRequest(requestId, symbol, timeframe)) {
      state.socket = null; setFeed(false, 'Live stream disconnected');
    }
  };
  socket.onerror = () => {
    if (socket === state.socket && currentMarketRequest(requestId, symbol, timeframe)) setFeed(false, 'Live stream error');
  };
}`
      );

      source = replaceBlock(
        source,
        'async function loadContext() {',
        '\n\nasync function refreshWatchlist() {',
        `async function loadContext(symbol = state.symbol, timeframe = state.timeframe, requestId = state.marketRequestId) {
  try {
    const payload = await api(\`/v1/forecast-context/\${symbol}?timeframe=\${timeframe}\`);
    if (currentMarketRequest(requestId, symbol, timeframe)) renderContext(payload);
  } catch {}
}`
      );

      source = source.replace(
        "const stale=state.lastQuoteAt&&Date.now()-state.lastQuoteAt>15000;",
        "const stale=state.socket?.readyState===WebSocket.OPEN&&state.lastStreamAt&&Date.now()-state.lastStreamAt>15000;"
      );

      const script = document.createElement('script');
      script.textContent = `${source}\n//# sourceURL=${scriptUrl.href}`;
      document.body.appendChild(script);
    })
    .catch(fail);
})();
