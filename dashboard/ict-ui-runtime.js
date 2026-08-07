(() => {
  const NativeFetch = window.fetch.bind(window);
  const NativeWebSocket = window.WebSocket;
  const runtimeUrl = document.currentScript?.src || window.location.href;
  let latestPayload = null;
  let renderTimer = null;

  const number = (value, digits = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : '—';
  };

  const words = value => String(value || 'unknown').replaceAll('_', ' ').toUpperCase();

  function extract(payload) {
    if (!payload || typeof payload !== 'object') return null;
    const revision = payload.revision || {};
    const multi = payload.multi_timeframe || {};
    const marketContext = revision.market_context || multi.market_context || payload.market_context || {};
    const ict = payload.ict_context
      || revision.ict_context
      || marketContext.ict
      || multi.ict_context
      || null;
    const hierarchy = multi.hierarchy || payload.hierarchy || null;
    if (ict || hierarchy) return { ict, hierarchy, marketContext, gate: revision.regime_gate || payload.regime_gate };

    if (Array.isArray(payload.forecasts) && payload.forecasts.length) {
      return extract(payload.forecasts[0]);
    }
    return null;
  }

  function tone(card, value) {
    if (!card) return;
    card.classList.remove('positive', 'negative', 'warning');
    if (value) card.classList.add(value);
  }

  function setCard(prefix, label, value, detail, cardTone = '') {
    const card = document.getElementById(`${prefix}ContextCard`);
    const valueNode = document.getElementById(`${prefix}ContextValue`);
    const detailNode = document.getElementById(`${prefix}ContextDetail`);
    const labelNode = card?.querySelector('span');
    if (!card || !valueNode || !detailNode || !labelNode) return false;
    labelNode.textContent = label;
    valueNode.textContent = value;
    detailNode.textContent = detail;
    tone(card, cardTone);
    return true;
  }

  function render(payload) {
    const extracted = extract(payload);
    if (!extracted) return;
    const ict = extracted.ict || {};
    const hierarchy = extracted.hierarchy || {};
    const marketContext = extracted.marketContext || {};
    const structure = ict.structure || {};
    const liquidity = ict.liquidity || {};
    const sweep = liquidity.sweep || null;
    const draw = liquidity.draw || null;
    const fvg = ict.fair_value_gaps || {};
    const dealing = ict.dealing_range || {};
    const displacement = ict.displacement || {};
    const session = ict.session || {};
    const event = ict.event_risk || {};
    const setup = ict.setup || {};
    const volatility = marketContext.volatility || {};

    const structureBias = structure.bias || 'sideways';
    const structureValue = `${words(structure.state || structureBias)} · ${number(structure.strength_pct, 0)}%`;
    const structureDetail = [
      structure.bos ? `BOS ${words(structure.bos)}` : null,
      structure.choch ? `CHoCH ${words(structure.choch)}` : null,
      `Setup ${words(setup.state || 'waiting')}`,
    ].filter(Boolean).join(' · ');
    setCard(
      'trend',
      'Structure',
      structureValue,
      structureDetail,
      structureBias === 'bullish' ? 'positive' : structureBias === 'bearish' ? 'negative' : 'warning',
    );

    const liquidityValue = sweep
      ? `${words(sweep.side)} SWEPT`
      : draw
        ? `DRAW → ${words(draw.type)}`
        : 'NO CLEAR LIQUIDITY DRAW';
    const activeZone = structureBias === 'bearish' ? fvg.nearest_bearish : fvg.nearest_bullish;
    const liquidityDetail = [
      activeZone ? `FVG ${number(activeZone.low, 2)}–${number(activeZone.high, 2)}` : null,
      dealing.zone ? `${words(dealing.zone)} ${number(dealing.position_pct, 0)}%` : null,
    ].filter(Boolean).join(' · ') || 'Waiting for structure';
    setCard(
      'range',
      'Liquidity',
      liquidityValue,
      liquidityDetail,
      sweep?.direction === 'bullish' ? 'positive' : sweep?.direction === 'bearish' ? 'negative' : '',
    );

    const sessionValue = `${words(session.name || 'unknown')}${session.killzone ? ' KILLZONE' : ''} · ${words(volatility.state || 'unknown')}`;
    const sessionDetail = [
      displacement.active ? `${words(displacement.direction)} displacement ${number(displacement.score_pct, 0)}%` : `Displacement ${number(displacement.score_pct, 0)}%`,
      event.blocked ? 'EVENT BLOCK' : event.nearest ? `Event ${number(event.nearest.minutes, 0)}m` : 'Event clear',
    ].join(' · ');
    setCard(
      'volatility',
      'Session / volatility',
      sessionValue,
      sessionDetail,
      event.blocked ? 'negative' : displacement.active ? 'warning' : '',
    );

    const status = hierarchy.status || setup.state || 'waiting';
    const tradeAllowed = hierarchy.trade_allowed === true;
    const hierarchyValue = tradeAllowed
      ? `${words(hierarchy['1h']?.bias || setup.bias)} SETUP · ${number(payload.multi_timeframe?.agreement_pct ?? payload.agreement_pct, 0)}%`
      : `NO TRADE · ${words(status)}`;
    const hierarchyDetail = hierarchy['1h']
      ? `1H ${words(hierarchy['1h'].bias)} · 15m ${words(hierarchy['15m']?.state)} · 5m ${words(hierarchy['5m']?.trigger || 'WAIT')}`
      : `${words(setup.bias)} · Quality ${number(setup.quality_pct, 0)}%`;
    setCard(
      'alignment',
      'ICT alignment',
      hierarchyValue,
      hierarchyDetail,
      tradeAllowed ? 'positive' : status === 'conflict' || status === 'event_block' ? 'negative' : 'warning',
    );

    document.getElementById('forecastIntelligenceOverlay')?.classList.add('visible');
  }

  function schedule(payload) {
    const extracted = extract(payload);
    if (!extracted) return;
    latestPayload = payload;
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => render(latestPayload), 80);
  }

  window.fetch = async (...args) => {
    const response = await NativeFetch(...args);
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      response.clone().json().then(schedule).catch(() => {});
    }
    return response;
  };

  if (NativeWebSocket) {
    class ICTContextWebSocket extends NativeWebSocket {
      constructor(...args) {
        super(...args);
        super.addEventListener('message', event => {
          try {
            schedule(JSON.parse(event.data));
          } catch (_) {}
        });
      }
    }
    window.WebSocket = ICTContextWebSocket;
  }

  const observer = new MutationObserver(() => {
    if (latestPayload && document.getElementById('forecastIntelligenceOverlay')) {
      schedule(latestPayload);
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Load the historical replay only after the base dashboard has completed its
  // own wireUI() pass. Loading it earlier would remove the legacy #runReplay node
  // while the base app is still trying to attach its handler.
  function loadHistoricalReplayWhenReady() {
    if (document.querySelector('script[data-traid-historical-replay]')) return;
    const legacyReplayButton = document.getElementById('runReplay');
    const replayPanel = document.querySelector('[data-content="replay"]');
    if (!legacyReplayButton || typeof legacyReplayButton.onclick !== 'function' || !replayPanel) {
      setTimeout(loadHistoricalReplayWhenReady, 60);
      return;
    }

    const script = document.createElement('script');
    script.dataset.traidHistoricalReplay = 'true';
    script.src = new URL('./historical-replay-runtime.js', runtimeUrl).href;
    script.onerror = () => console.error('Could not load historical-replay-runtime.js');
    document.head.appendChild(script);
  }

  // Chart polish is intentionally isolated from app-loader.js. Waiting for the
  // same bootstrap signal keeps its wrappers away from temporal-dead-zone races.
  function loadChartPolishWhenReady() {
    if (document.querySelector('script[data-traid-chart-polish]')) return;
    const legacyReplayButton = document.getElementById('runReplay');
    const chartNode = document.getElementById('chart');
    if (!legacyReplayButton || typeof legacyReplayButton.onclick !== 'function' || !chartNode) {
      setTimeout(loadChartPolishWhenReady, 60);
      return;
    }

    const script = document.createElement('script');
    script.dataset.traidChartPolish = 'true';
    script.src = new URL('./chart-polish-runtime.js', runtimeUrl).href;
    script.onerror = () => console.error('Could not load chart-polish-runtime.js');
    document.head.appendChild(script);
  }

  setTimeout(loadHistoricalReplayWhenReady, 0);
  setTimeout(loadChartPolishWhenReady, 0);
})();