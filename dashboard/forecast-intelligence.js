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
        .forecast-confidence-pill.calibrating { color:#bfdbfe; border-color:rgba(96,165,250,.30); background:rgba(30,64,175,.20); }

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
    const pill = document.getElementById('forecastConfidencePill');
    if (!pill || !confidence) return;

    if (confidence.available === false || confidence.calibrated === false) {
      const count = Number(confidence.independent_forecasts) || 0;
      const required = Number(confidence.required_forecasts) || 30;
      pill.className = 'forecast-confidence-pill visible calibrating';
      pill.textContent = `CALIBRATING ${count}/${required}`;
      pill.title = [
        'No confidence percentage is shown until enough independent realized forecasts exist.',
        `Market: ${confidence.symbol || '—'} ${confidence.timeframe || '—'}`,
        `Horizon: candle ${confidence.horizon || '—'}`,
        `Regime: ${confidence.regime || 'unknown'}`,
        `Independent forecasts: ${count}/${required}`,
      ].join('\n');
      return;
    }

    const score = Number(confidence.score_pct);
    if (!Number.isFinite(score)) return;
    const grade = confidence.grade || (score >= 65 ? 'high' : score >= 55 ? 'medium' : 'low');
    const components = confidence.components || {};
    const vote = confidence.path_vote || {};

    pill.className = `forecast-confidence-pill visible ${grade}`;
    pill.textContent = `CONFIDENCE ${number(score, 0)}%`;
    pill.title = [
      `Calibrated confidence: ${number(score, 1)}% (${grade})`,
      `Independent forecasts: ${confidence.independent_forecasts ?? '—'}`,
      `Market/regime: ${confidence.symbol || '—'} ${confidence.timeframe || '—'} · ${confidence.regime || 'unknown'}`,
      `Horizon: candle ${confidence.horizon || '—'}`,
      `Direction accuracy: ${number(components.direction_accuracy_pct, 1)}%`,
      `Distance accuracy: ${number(components.distance_accuracy_pct, 1)}%`,
      `Current path vote: ${String(vote.direction || 'unknown').toUpperCase()} ${number(vote.agreement_pct, 1)}%`,
      `Paths: ${confidence.paths ?? confidence.sample_count ?? NORMAL_SAMPLES}`,
    ].join('\n');
  }

  function renderMarketContext(context = {}, gate = null) {
    installUI();
    const overlay = document.getElementById('forecastIntelligenceOverlay');
    if (overlay) overlay.classList.add('visible');

    const trend = context.trend || {};
    const range = context.range || {};
    const volatility = context.volatility || {};
    const breakout = context.breakout || {};

    const trendTone = trend.direction === 'bullish' ? 'positive' : trend.direction === 'bearish' ? 'negative' : 'warning';
    const breakoutText = breakout.active
      ? `${String(breakout.direction || '').toUpperCase()} BREAKOUT ${number(breakout.score_pct, 0)}%`
      : `${String(context.regime || 'unknown').toUpperCase()} regime`;
    setCard(
      'trendContextCard', 'trendContextValue', 'trendContextDetail',
      `${String(trend.direction || 'unknown').toUpperCase()} · ${number(trend.strength_pct, 0)}%`,
      breakoutText, trendTone,
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

    if (gate?.applied) {
      const status = gate.trade_allowed ? 'GATED · FILTERED' : 'NO TRADE · GATED';
      setCard(
        'alignmentContextCard', 'alignmentContextValue', 'alignmentContextDetail',
        status,
        `${String(gate.raw_vote_direction || 'unknown').toUpperCase()} vote ${number(gate.raw_vote_pct, 0)}% · ${gate.selected_paths || 0}/${gate.total_paths || 0} paths`,
        gate.trade_allowed ? 'warning' : 'negative',
      );
    }
  }

  function renderContext(payload) {
    installUI();
    const multi = payload?.multi_timeframe;
    if (!multi) return;

    renderMarketContext(multi.market_context || {});
    const readings = (multi.readings || [])
      .map(item => `${item.timeframe} ${String(item.direction || '?').slice(0, 4).toUpperCase()}`)
      .join(' · ');
    const alignmentTone = multi.trade_allowed ? 'positive' : multi.contradiction ? 'negative' : 'warning';
    const alignmentValue = multi.trade_allowed
      ? `${String(multi.trade_bias || multi.consensus).toUpperCase()} · ${number(multi.agreement_pct, 0)}%`
      : multi.contradiction
        ? 'NO TRADE · CONFLICT'
        : multi.complete
          ? 'NO TRADE · MIXED'
          : 'WAIT · INCOMPLETE';
    setCard(
      'alignmentContextCard', 'alignmentContextValue', 'alignmentContextDetail',
      alignmentValue, readings || '5m · 15m · 1h', alignmentTone,
    );

    const existingConsensus = document.getElementById('timeframeConsensus');
    const existingAgreement = document.getElementById('timeframeAgreement');
    if (existingConsensus) existingConsensus.textContent = multi.trade_allowed ? String(multi.trade_bias).toUpperCase() : 'NO TRADE';
    if (existingAgreement) existingAgreement.textContent = `5m/15m/1h · ${number(multi.agreement_pct, 0)}% aligned`;
  }

  function inspectPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    const hasProjection = Array.isArray(payload.projection) && payload.projection.length > 0;
    const revision = payload.revision || {};
    const confidence = payload.confidence || revision.model_confidence || payload.uncertainty?.confidence;
    if (hasProjection && confidence) renderConfidence(confidence);
    if (hasProjection && revision.market_context) {
      renderMarketContext(revision.market_context, revision.regime_gate || payload.regime_gate);
    }
    if (payload.multi_timeframe) renderContext(payload);
  }

  function isInitialForecastHistory(url, init) {
    if (String(init?.method || 'GET').toUpperCase() !== 'GET') return false;
    try {
      const parsed = new URL(url, window.location.href);
      return parsed.pathname.includes('/v1/forecasts/')
        && parsed.searchParams.get('limit') === '25';
    } catch (_) {
      return false;
    }
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
    if (!contentType.includes('application/json')) return response;

    try {
      const payload = await response.clone().json();
      inspectPayload(payload);

      // Do not let the dashboard bypass the intrabar-aware server request with
      // a completed-candle-only cached forecast. The backend performs safe
      // one-per-forming-candle deduplication across tabs.
      if (response.ok && isInitialForecastHistory(url, init)) {
        return new Response(
          JSON.stringify({ ...payload, forecasts: [] }),
          {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers,
          },
        );
      }
    } catch (_) {}
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
    renderMarketContext,
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
