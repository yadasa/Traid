(() => {
  let installed = false;
  let timerStartedAt = null;
  let timerHandle = null;
  let lastElapsedSeconds = null;
  let toastWrapped = false;

  function installStyles() {
    if (document.getElementById('traidRuntimePolishV2Styles')) return;
    const style = document.createElement('style');
    style.id = 'traidRuntimePolishV2Styles';
    style.textContent = `
      /* Keep the live-candle SVG inside the chart's paint box. It still sits
         above the chart canvas, but can no longer float over toolbar/cards when
         the chart is vertically panned or the page scrolls. */
      .chart-wrap {
        isolation:isolate !important;
        overflow:hidden !important;
        contain:paint;
      }
      #traidLiveCandleClone.traid-live-candle-clone {
        z-index:2 !important;
        overflow:hidden !important;
        pointer-events:none !important;
      }
      .chart-watermark,
      .forecast-label,
      .stale-overlay { z-index:5 !important; }
      .chart-toolbar,
      .chart-status-row { position:relative; z-index:12; }

      .historical-replay-generation-timer {
        display:inline-flex;
        align-items:center;
        justify-content:center;
        min-width:86px;
        height:34px;
        padding:0 11px;
        border:1px solid rgba(96,165,250,.18);
        border-radius:9px;
        background:rgba(59,130,246,.07);
        color:#bfdbfe;
        font-size:11px;
        font-weight:800;
        font-variant-numeric:tabular-nums;
        letter-spacing:.02em;
      }
      .historical-replay-generation-timer.running {
        border-color:rgba(45,212,191,.30);
        color:#99f6e4;
        box-shadow:0 0 18px rgba(45,212,191,.08);
      }
      .historical-replay-generation-timer::before {
        content:'GEN ';
        margin-right:5px;
        color:#7182aa;
        font-size:8px;
        letter-spacing:.09em;
      }
      @media (max-width:700px) {
        .historical-replay-generation-timer { min-width:76px; height:32px; }
      }
    `;
    document.head.appendChild(style);
  }

  function timerNode() {
    let node = document.getElementById('historicalReplayGenerationTimer');
    if (node) return node;
    const transport = document.querySelector('.historical-replay-transport');
    const progress = document.getElementById('historicalReplayProgress');
    if (!transport || !progress) return null;
    node = document.createElement('span');
    node.id = 'historicalReplayGenerationTimer';
    node.className = 'historical-replay-generation-timer';
    node.textContent = '0.0s';
    transport.insertBefore(node, progress);
    return node;
  }

  function updateTimer() {
    if (timerStartedAt == null) return;
    const elapsed = Math.max(0, (performance.now() - timerStartedAt) / 1000);
    const node = timerNode();
    if (node) node.textContent = `${elapsed.toFixed(1)}s`;
  }

  function startTimer() {
    stopTimer(false);
    timerStartedAt = performance.now();
    lastElapsedSeconds = null;
    const node = timerNode();
    if (node) {
      node.classList.add('running');
      node.textContent = '0.0s';
    }
    timerHandle = window.setInterval(updateTimer, 100);
  }

  function stopTimer(commit = true) {
    if (timerHandle != null) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
    if (timerStartedAt != null && commit) {
      lastElapsedSeconds = Math.max(0, (performance.now() - timerStartedAt) / 1000);
      const node = timerNode();
      if (node) node.textContent = `${lastElapsedSeconds.toFixed(1)}s`;
    }
    const node = timerNode();
    node?.classList.remove('running');
    timerStartedAt = null;
  }

  function wrapToast() {
    if (toastWrapped || typeof window.toast !== 'function') return false;
    const baseToast = window.toast;
    window.toast = function timedReplayToast(message, type = '') {
      let nextMessage = message;
      if (type === 'success' && /^Historical Kronos forecast ready/i.test(String(message))) {
        if (timerStartedAt != null) stopTimer(true);
        if (Number.isFinite(lastElapsedSeconds)) {
          const modelMatch = String(message).match(/in\s+([0-9.]+)s/i);
          const modelText = modelMatch ? ` · model ${modelMatch[1]}s` : '';
          nextMessage = `Historical Kronos forecast ready · total ${lastElapsedSeconds.toFixed(1)}s${modelText}.`;
        }
      }
      return baseToast(nextMessage, type);
    };
    toastWrapped = true;
    return true;
  }

  function installReplayTimer() {
    const generate = document.getElementById('historicalReplayGenerate');
    const progress = document.getElementById('historicalReplayProgress');
    if (!generate || !progress) return false;
    timerNode();

    if (generate.dataset.elapsedTimerInstalled !== 'true') {
      generate.dataset.elapsedTimerInstalled = 'true';
      generate.addEventListener('click', () => {
        if (!generate.disabled) startTimer();
      }, true);
    }

    if (progress.dataset.elapsedTimerObserver !== 'true') {
      progress.dataset.elapsedTimerObserver = 'true';
      const observer = new MutationObserver(() => {
        if (timerStartedAt == null) return;
        if (progress.classList.contains('success') || progress.classList.contains('error')) {
          stopTimer(true);
        }
      });
      observer.observe(progress, { attributes: true, childList: true, subtree: true, characterData: true });
    }
    return true;
  }

  function install() {
    installStyles();
    wrapToast();
    const timerReady = installReplayTimer();
    if (!timerReady || !toastWrapped) {
      setTimeout(install, 120);
      return;
    }
    installed = true;
  }

  install();
})();
