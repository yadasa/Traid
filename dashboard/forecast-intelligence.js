(() => {
  const NORMAL_SAMPLES = 10;
  const ADVANCED_PATHS = 14;
  const nativeFetch = window.fetch.bind(window);
  const NativeWebSocket = window.WebSocket;

  const number = (value, digits = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : '—';
  };

  function installUI() {
    const pathInput = document.getElementById('uncertaintyPaths');
    if (pathInput) pathInput.value = String(ADVANCED_PATHS);

    const statusRow = document.querySelector('.chart-status-row');
    const chartWrap = document.querySelector('.chart-wrap');
    if (!statusRow || !chartWrap) return;

    if (!document.getElementById('forecastIntelligenceStyle')) {
      const style = document.createElement('style');
      style.id = 'forecastIntelligenceStyle';
      style.textContent = `
        .forecast-confidence-pill {
          display:none; align-items:center; gap:6px; min-height:26px; padding:0 10px;
          border:1px solid rgba(148,163,184,.2); border-radius:999px;
          background:rgba(15,23,42,.82); color:#cbd5e1; font-size:10px;
          font-weight:850; letter-spacing:.05em; white-space:nowrap;
        }
        .forecast-confidence-pill.visible { display:inline-flex; }
        .forecast-confidence-pill.high { color:#99f6e4; border-color:rgba(45,212,191,.35); background:rgba(13,78,69,.30); }
        .forecast-confidence-pill.medium { color:#fde68a; border-color:rgba(245,158,11,.32); background:rgba(120,53,15,.28); }
        .forecast-confidence-pill.low { color:#fecdd3; border-color:rgba(251,113,133,.34); background:rgba(127,29,29,.26); }

        .forecast-intelligence-overlay {
          position:absolute; left:12px; right:12px; bottom:12px; z-index:4;
          display:none; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px;
          pointer-events:none;
        }
        .forecast-intelligence-overlay.visible { display:grid; }
        .forecast-intelligence-card {
          min-width:0; padding:8px 10px; border:1px solid rgba(148,163,184,.16);
          border-radius:9px; background:rgba(7,11,27,.82); backdrop-filter:blur(10px);
          box-shadow:0 8px 24px rgba(0,0,0,.24);
        }
        .forecast-intelligence-card span {
          display:block; color:#7f8aa8; font-size:8px; font-weight:800;
          letter-spacing:.08em; text-transform:uppercase;
        }
        .forecast-intelligence-card strong {
          display:block; margin-top:3px; color:#e5e7eb; font-size:11px;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        }
        .forecast-intelligence-card small {
          display:block; margin-top:2px; color:#7f8aa8; font-size:8px;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        }
        .forecast-intelligence-card.positive strong { color:#99f6e4; }
        .forecast-intelligence-card.negative strong { color:#fecdd3; }
        .forecast-intelligence-card.warning strong { color:#fde68a; }

        @media (max-width:900px) {
          .forecast-intelligence-overlay { grid-template-columns:repeat(2,minmax(0,1fr)); }
        }
        @media (max-width:540px) {
          .forecast-intelligence-overlay { left:8px; right:8px; bottom:8px; gap:5px; }
          .forecast-intelligence-card { padding:6px 7px; }
        }
      `;
      document.head.appendChild(style);
    }

    if (!document.getElementById('forecastConfidencePill')) {
      const pill = document.createElement('span');
      pill.id = 'forecastConfidencePill';
      pill.className = 'forecast-confidence-pill';
      pill.textContent = 'CONFIDENCE —';
      statusRow.appendChild(pill);
    }

    if (!document.getElementById('forecastIntelligenceOverlay')) {
      const overlay = document.createElement('div');
      overlay.id = 'forecastIntelligenceOverlay';
      overlay.className = 'forecast-intelligence-overlay';
      overlay.innerHTML = `
        <article class="forecast-intelligence-card" id="trendContextCard"><span>Trend</span><strong id="trendContextValue">Waiting</strong><small id="trendContextDetail">5m · 15m · 1h</small></article>
        <article class="forecast-intelligence-card" id="rangeContextCard"><span>Range</span><strong id="rangeContextValue">Waiting</strong><small id="rangeContextDetail">Market structure</small></article>
        <article class="forecast-intelligence-card" id="volatilityContextCard"><span>Volatility</span><strong id="volatilityContextValue">Waiting</strong><small id="volatilityContextDetail">ATR context</small></article>
        <article class="forecast-intelligence-card" id="alignmentContextCard"><span>1-hour alignment</span><strong id="alignmentContextValue">Waiting</strong><small id="alignmentContextDetail">5m · 15m · 1h</small></article>
      `;
      chartWrap.appendChild(overlay);
    }
  }

  function reset() {
    installUI();
    const pill = document.getElementById('forecastConfidencePill');
    const overlay = document.getElementById('forecastIntelligenceOverlay');
    if (pill) {
      pill.className = 'forecast-confidence-pill';
      pill.textContent = 'CONFIDENCE —';
      pill.removeAttribute('title');
    }
    if (overlay) overlay.classList.remove('visible');
  }

  function setCard(id, valueId, detailId, value, detail, tone = '') {
    const card = document.getElementById(id);
    const valueNode = document.getElementById(valueId);
    const detailNode = document.getElementById(detailId);
    if (!card || !valueNode || !detailNode) return;
    card.classList.remove('positive', 'negative', 'warning');
    if (tone) card.classList.add(tone);
    valueNode.textContent = value;
    detailNode.textContent = detail;
  }

  function renderConfidence(confidence) {
    installUI();
    if (!confidence) return;
    const score = Number(confidence.score_pct);
    if (!Number.isFinite(score)) return;

    const grade = confidence.grade || (score >= 70 ? 'high' : score >= 58 ? 'medium' : 'low');
    const pill = document.getElementById('forecastConfidencePill');
    if (!pill) return;

    pill.className = `forecast-confidence-pill visible ${grade}`;
    pill.textContent = `CONFIDENCE ${number(score, 0)}%`;
    const components = confidence.components || {};
    pill.title = [
      `Model confidence: ${number(score, 1)}% (${grade})`,
      `Samples: ${confidence.sample_count ?? NORMAL_SAMPLES}`,
      `Paths: ${confidence.paths ?? 1}`,
      `Path agreement: ${number(components.path_agreement_pct, 1)}%`,
      `Forecast stability: ${number(components.stability_pct, 1)}%`,
      `Historical direction: ${number(components.historical_accuracy_pct, 1)}%`,
      'Confidence is a decision-quality score, not a guaranteed win probability.',
    ].join('\n');
  }

  function renderContext(payload) {
    installUI();
    const multi = payload?.multi_timeframe;
    if (!multi) return;

    const overlay = document.getElementById('forecastIntelligenceOverlay');
    if (overlay) overlay.classList.add('visible');

    const context = multi.market_context || {};
    const trend = context.trend || {};
    const range = context.range || {};
    const volatility = context.volatility || {};

    const trendTone = trend.direction === 'bullish' ? 'positive' : trend.direction === 'bearish' ? 'negative' : 'warning';
    setCard(
      'trendContextCard', 'trendContextValue', 'trendContextDetail',
      `${String(trend.direction || 'unknown').toUpperCase()} · ${number(trend.strength_pct, 0)}%`,
      `${String(context.regime || 'unknown').toUpperCase()} regime`, trendTone,
    );

    const rangeTone = String(range.state || '').includes('upper') ? 'positive' : String(range.state || '').includes('lower') ? 'negative' : '';
    setCard(
      'rangeContextCard', 'rangeContextValue', 'rangeContextDetail',
      String(range.state || 'unknown').toUpperCase(),
      `Position ${number(range.position_pct, 0)}% · width ${number(range.width_pct, 2)}%`, rangeTone,
    );

    const volatilityTone = volatility.state === 'high' ? 'warning' : volatility.state === 'low' ? '' : 'positive';
    setCard(
      'volatilityContextCard', 'volatilityContextValue', 'volatilityContextDetail',
      String(volatility.state || 'unknown').toUpperCase(),
      `ATR ${number(volatility.atr_pct, 3)}% · relative ${number(volatility.relative_pct, 0)}%`, volatilityTone,
    );

    const readings = (multi.readings || [])
      .map(item => `${item.timeframe} ${String(item.direction || '?').slice(0, 4).toUpperCase()}`)
      .join(' · ');
    const alignmentTone = multi.aligned ? 'positive' : multi.contradiction ? 'negative' : 'warning';
    const alignmentValue = multi.aligned
      ? `${String(multi.trade_bias || multi.consensus).toUpperCase()} · ${number(multi.agreement_pct, 0)}%`
      : multi.contradiction
        ? 'NO TRADE · CONFLICT'
        : 'WAIT · INCOMPLETE';
    setCard(
      'alignmentContextCard', 'alignmentContextValue', 'alignmentContextDetail',
      alignmentValue, readings || '5m · 15m · 1h', alignmentTone,
    );

    const existingConsensus = document.getElementById('timeframeConsensus');
    const existingAgreement = document.getElementById('timeframeAgreement');
    if (existingConsensus) existingConsensus.textContent = multi.aligned ? String(multi.trade_bias).toUpperCase() : 'NO TRADE';
    if (existingAgreement) existingAgreement.textContent = `5m/15m/1h · ${number(multi.agreement_pct, 0)}% aligned`;
  }

  function inspectPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    const confidence = payload.confidence || payload.revision?.model_confidence || payload.uncertainty?.confidence;
    if (confidence && Array.isArray(payload.projection) && payload.projection.length) renderConfidence(confidence);
    if (payload.multi_timeframe) renderContext(payload);
  }

  window.fetch = async (input, init = {}) => {
    let nextInit = init;
    const url = typeof input === 'string' ? input : input?.url || '';

    if (
      url.includes('/v1/forecast')
      && !url.includes('/v1/forecasts/')
      && String(init.method || 'GET').toUpperCase() === 'POST'
      && init.body
    ) {
      try {
        const body = JSON.parse(init.body);
        body.sample_count = Math.max(NORMAL_SAMPLES, Number(body.sample_count) || 0);
        if (body.advanced) {
          body.uncertainty_paths = Math.max(ADVANCED_PATHS, Number(body.uncertainty_paths) || 0);
        }
        nextInit = { ...init, body: JSON.stringify(body) };
      } catch (_) {}
    }

    const response = await nativeFetch(input, nextInit);
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      response.clone().json().then(inspectPayload).catch(() => {});
    }
    return response;
  };

  class IntelligenceWebSocket extends NativeWebSocket {
    constructor(url, protocols) {
      if (protocols === undefined) super(url);
      else super(url, protocols);
      this.addEventListener('message', event => {
        try { inspectPayload(JSON.parse(event.data)); } catch (_) {}
      });
    }
  }

  Object.defineProperties(IntelligenceWebSocket, {
    CONNECTING: { value: NativeWebSocket.CONNECTING },
    OPEN: { value: NativeWebSocket.OPEN },
    CLOSING: { value: NativeWebSocket.CLOSING },
    CLOSED: { value: NativeWebSocket.CLOSED },
  });

  window.WebSocket = IntelligenceWebSocket;
  window.TraidForecastIntelligence = {
    normalSamples: NORMAL_SAMPLES,
    advancedPaths: ADVANCED_PATHS,
    renderConfidence,
    renderContext,
    reset,
  };

  const install = () => {
    installUI();
    document.getElementById('symbolSelect')?.addEventListener('change', reset);
    document.getElementById('refreshForecast')?.addEventListener('click', reset);
    document.querySelectorAll('[data-timeframe]').forEach(button => button.addEventListener('click', reset));
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
