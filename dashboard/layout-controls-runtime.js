(() => {
  const STORAGE = Object.freeze({
    chartRatio: 'traid.layout.chartRatio',
    marketsCollapsed: 'traid.layout.marketsCollapsed',
    executionCollapsed: 'traid.layout.executionCollapsed',
  });
  const DEFAULT_CHART_RATIO = 0.62;
  const MIN_CHART_HEIGHT = 350;
  const MIN_LOWER_HEIGHT = 150;
  const SPLITTER_HEIGHT = 10;
  const DESKTOP_MEDIA = window.matchMedia('(min-width: 1001px)');

  let chartRatio = clampRatio(Number(localStorage.getItem(STORAGE.chartRatio)) || DEFAULT_CHART_RATIO);
  let marketsCollapsed = localStorage.getItem(STORAGE.marketsCollapsed) === 'true';
  let executionCollapsed = localStorage.getItem(STORAGE.executionCollapsed) === 'true';
  let pseudoFullscreen = false;
  let resizeObserver = null;
  let initialized = false;

  function clampRatio(value) {
    return Math.max(0.38, Math.min(0.82, Number.isFinite(value) ? value : DEFAULT_CHART_RATIO));
  }

  function installStyles() {
    if (document.getElementById('traidLayoutControlStyles')) return;
    const style = document.createElement('style');
    style.id = 'traidLayoutControlStyles';
    style.textContent = `
      .app-shell.traid-layout-controls {
        transition:grid-template-columns 190ms ease;
      }
      .traid-side-collapse {
        flex:0 0 auto;
        width:28px !important;
        height:28px !important;
        min-width:28px !important;
        min-height:28px !important;
        padding:0 !important;
        border-radius:8px !important;
        font-size:15px !important;
        line-height:1;
      }
      .traid-chart-splitter {
        position:relative;
        z-index:20;
        width:100%;
        height:${SPLITTER_HEIGHT}px;
        cursor:ns-resize;
        touch-action:none;
        user-select:none;
      }
      .traid-chart-splitter::before {
        content:'';
        position:absolute;
        left:50%;
        top:4px;
        width:72px;
        height:2px;
        transform:translateX(-50%);
        border-radius:999px;
        background:rgba(139,158,213,.24);
        box-shadow:0 0 0 1px rgba(5,7,19,.45);
        transition:160ms ease;
      }
      .traid-chart-splitter:hover::before,
      .traid-chart-splitter.dragging::before {
        width:104px;
        background:linear-gradient(90deg,rgba(96,165,250,.8),rgba(139,92,246,.8));
        box-shadow:0 0 12px rgba(96,165,250,.24);
      }
      body.traid-layout-dragging { cursor:ns-resize !important; user-select:none !important; }
      .traid-fullscreen-button {
        min-width:34px !important;
        width:34px;
        padding:0 !important;
        font-size:14px !important;
      }
      #chartView:fullscreen {
        width:100vw !important;
        height:100vh !important;
        max-width:none !important;
        max-height:none !important;
        margin:0 !important;
        border:0 !important;
        border-radius:0 !important;
        grid-template-rows:52px 30px minmax(0,1fr) auto !important;
        background:#070b1b !important;
      }
      #chartView.traid-pseudo-fullscreen {
        position:fixed !important;
        inset:0 !important;
        z-index:10000 !important;
        width:100vw !important;
        height:100vh !important;
        margin:0 !important;
        border:0 !important;
        border-radius:0 !important;
        grid-template-rows:52px 30px minmax(0,1fr) auto !important;
        background:#070b1b !important;
      }
      #chartView:fullscreen .chart-wrap,
      #chartView.traid-pseudo-fullscreen .chart-wrap { min-height:0 !important; }

      @media (min-width:1001px) {
        .app-shell.traid-markets-collapsed:not(.traid-execution-collapsed) {
          grid-template-columns:44px minmax(0,1fr) 338px !important;
        }
        .app-shell.traid-execution-collapsed:not(.traid-markets-collapsed) {
          grid-template-columns:232px minmax(0,1fr) 44px !important;
        }
        .app-shell.traid-markets-collapsed.traid-execution-collapsed {
          grid-template-columns:44px minmax(0,1fr) 44px !important;
        }
        #watchlistPanel.traid-side-collapsed > :not(.panel-heading),
        #tradePanel.traid-side-collapsed > :not(.panel-heading) {
          display:none !important;
        }
        #watchlistPanel.traid-side-collapsed .panel-heading,
        #tradePanel.traid-side-collapsed .panel-heading {
          min-height:100% !important;
          height:100% !important;
          padding:0 !important;
          justify-content:center !important;
          align-items:center !important;
          border-bottom:0 !important;
        }
        #watchlistPanel.traid-side-collapsed .panel-heading > :not(.traid-side-collapse),
        #tradePanel.traid-side-collapsed .panel-heading > :not(.traid-side-collapse) {
          display:none !important;
        }
        #watchlistPanel.traid-side-collapsed,
        #tradePanel.traid-side-collapsed {
          overflow:hidden !important;
        }
      }
      @media (max-width:1000px) {
        .traid-side-collapse,
        .traid-chart-splitter { display:none !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function nodes() {
    return {
      shell: document.querySelector('.app-shell'),
      workspace: document.querySelector('.workspace'),
      chartView: document.getElementById('chartView'),
      analysisView: document.getElementById('analysisView'),
      watchlist: document.getElementById('watchlistPanel'),
      trade: document.getElementById('tradePanel'),
      toolbar: document.querySelector('#chartView .chart-toolbar'),
    };
  }

  function scheduleChartResize() {
    const signal = () => window.dispatchEvent(new Event('resize'));
    requestAnimationFrame(() => {
      signal();
      requestAnimationFrame(signal);
    });
    setTimeout(signal, 220);
  }

  function availableWorkspaceHeight(workspace) {
    return Math.max(0, workspace?.clientHeight || 0);
  }

  function applyChartRatio({ persist = false } = {}) {
    const { workspace } = nodes();
    if (!workspace || !DESKTOP_MEDIA.matches || document.fullscreenElement || pseudoFullscreen) {
      if (workspace && !DESKTOP_MEDIA.matches) workspace.style.removeProperty('grid-template-rows');
      return;
    }
    const total = availableWorkspaceHeight(workspace);
    if (total <= MIN_CHART_HEIGHT + MIN_LOWER_HEIGHT + SPLITTER_HEIGHT) return;
    const usable = total - SPLITTER_HEIGHT;
    const maxChart = Math.max(MIN_CHART_HEIGHT, usable - MIN_LOWER_HEIGHT);
    const chartHeight = Math.max(MIN_CHART_HEIGHT, Math.min(maxChart, Math.round(usable * chartRatio)));
    chartRatio = clampRatio(chartHeight / usable);
    workspace.style.setProperty(
      'grid-template-rows',
      `${chartHeight}px ${SPLITTER_HEIGHT}px minmax(${MIN_LOWER_HEIGHT}px,1fr)`,
      'important',
    );
    workspace.style.setProperty('gap', '0', 'important');
    if (persist) localStorage.setItem(STORAGE.chartRatio, String(chartRatio));
    scheduleChartResize();
  }

  function installSplitter() {
    const { workspace, analysisView } = nodes();
    if (!workspace || !analysisView || document.getElementById('traidChartSplitter')) return;
    const splitter = document.createElement('div');
    splitter.id = 'traidChartSplitter';
    splitter.className = 'traid-chart-splitter';
    splitter.setAttribute('role', 'separator');
    splitter.setAttribute('aria-orientation', 'horizontal');
    splitter.setAttribute('aria-label', 'Resize chart vertically');
    splitter.title = 'Drag to resize chart · double-click to reset';
    workspace.insertBefore(splitter, analysisView);

    let pointerId = null;
    let startY = 0;
    let startHeight = 0;

    splitter.addEventListener('pointerdown', event => {
      if (!DESKTOP_MEDIA.matches) return;
      event.preventDefault();
      pointerId = event.pointerId;
      startY = event.clientY;
      startHeight = nodes().chartView?.getBoundingClientRect().height || 0;
      splitter.classList.add('dragging');
      document.body.classList.add('traid-layout-dragging');
      splitter.setPointerCapture?.(event.pointerId);
    });

    splitter.addEventListener('pointermove', event => {
      if (event.pointerId !== pointerId) return;
      const { workspace } = nodes();
      if (!workspace) return;
      const total = availableWorkspaceHeight(workspace);
      const maxChart = Math.max(MIN_CHART_HEIGHT, total - SPLITTER_HEIGHT - MIN_LOWER_HEIGHT);
      const nextHeight = Math.max(MIN_CHART_HEIGHT, Math.min(maxChart, startHeight + event.clientY - startY));
      const usable = Math.max(1, total - SPLITTER_HEIGHT);
      chartRatio = clampRatio(nextHeight / usable);
      workspace.style.setProperty(
        'grid-template-rows',
        `${Math.round(nextHeight)}px ${SPLITTER_HEIGHT}px minmax(${MIN_LOWER_HEIGHT}px,1fr)`,
        'important',
      );
      scheduleChartResize();
    });

    const finish = event => {
      if (event.pointerId !== pointerId) return;
      pointerId = null;
      splitter.classList.remove('dragging');
      document.body.classList.remove('traid-layout-dragging');
      try { splitter.releasePointerCapture?.(event.pointerId); } catch (_) {}
      localStorage.setItem(STORAGE.chartRatio, String(chartRatio));
      applyChartRatio();
    };
    splitter.addEventListener('pointerup', finish);
    splitter.addEventListener('pointercancel', finish);
    splitter.addEventListener('dblclick', () => {
      chartRatio = DEFAULT_CHART_RATIO;
      localStorage.setItem(STORAGE.chartRatio, String(chartRatio));
      applyChartRatio();
    });
  }

  function makeCollapseButton(side) {
    const isMarkets = side === 'markets';
    const panel = isMarkets ? nodes().watchlist : nodes().trade;
    const heading = panel?.querySelector('.panel-heading');
    if (!heading) return null;
    const id = isMarkets ? 'traidMarketsCollapse' : 'traidExecutionCollapse';
    let button = document.getElementById(id);
    if (button) return button;
    button = document.createElement('button');
    button.id = id;
    button.type = 'button';
    button.className = 'icon-button traid-side-collapse';
    heading.appendChild(button);
    button.addEventListener('click', () => {
      if (isMarkets) {
        marketsCollapsed = !marketsCollapsed;
        localStorage.setItem(STORAGE.marketsCollapsed, String(marketsCollapsed));
      } else {
        executionCollapsed = !executionCollapsed;
        localStorage.setItem(STORAGE.executionCollapsed, String(executionCollapsed));
      }
      applySidebarState();
    });
    return button;
  }

  function applySidebarState() {
    const { shell, watchlist, trade } = nodes();
    if (!shell || !watchlist || !trade) return;
    shell.classList.add('traid-layout-controls');
    const desktop = DESKTOP_MEDIA.matches;
    shell.classList.toggle('traid-markets-collapsed', desktop && marketsCollapsed);
    shell.classList.toggle('traid-execution-collapsed', desktop && executionCollapsed);
    watchlist.classList.toggle('traid-side-collapsed', desktop && marketsCollapsed);
    trade.classList.toggle('traid-side-collapsed', desktop && executionCollapsed);

    const leftButton = makeCollapseButton('markets');
    const rightButton = makeCollapseButton('execution');
    if (leftButton) {
      leftButton.textContent = marketsCollapsed && desktop ? '›' : '‹';
      leftButton.title = marketsCollapsed && desktop ? 'Expand Markets' : 'Collapse Markets';
      leftButton.setAttribute('aria-label', leftButton.title);
      leftButton.setAttribute('aria-expanded', String(!(marketsCollapsed && desktop)));
    }
    if (rightButton) {
      rightButton.textContent = executionCollapsed && desktop ? '‹' : '›';
      rightButton.title = executionCollapsed && desktop ? 'Expand Execution' : 'Collapse Execution';
      rightButton.setAttribute('aria-label', rightButton.title);
      rightButton.setAttribute('aria-expanded', String(!(executionCollapsed && desktop)));
    }
    scheduleChartResize();
  }

  function updateFullscreenButton() {
    const button = document.getElementById('traidChartFullscreen');
    const active = document.fullscreenElement === nodes().chartView || pseudoFullscreen;
    if (!button) return;
    button.textContent = active ? '×' : '⛶';
    button.title = active ? 'Exit chart fullscreen (Esc)' : 'Fullscreen chart';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', String(active));
  }

  function exitPseudoFullscreen() {
    if (!pseudoFullscreen) return;
    pseudoFullscreen = false;
    nodes().chartView?.classList.remove('traid-pseudo-fullscreen');
    updateFullscreenButton();
    applyChartRatio();
    scheduleChartResize();
  }

  async function toggleFullscreen() {
    const { chartView } = nodes();
    if (!chartView) return;
    if (document.fullscreenElement === chartView) {
      await document.exitFullscreen?.();
      return;
    }
    if (pseudoFullscreen) {
      exitPseudoFullscreen();
      return;
    }
    if (typeof chartView.requestFullscreen === 'function') {
      try {
        await chartView.requestFullscreen({ navigationUI: 'hide' });
        return;
      } catch (_) {
        // Browser denied native fullscreen; fall through to the chart-only CSS mode.
      }
    }
    pseudoFullscreen = true;
    chartView.classList.add('traid-pseudo-fullscreen');
    updateFullscreenButton();
    scheduleChartResize();
  }

  function installFullscreenButton() {
    const { toolbar } = nodes();
    if (!toolbar || document.getElementById('traidChartFullscreen')) return;
    const button = document.createElement('button');
    button.id = 'traidChartFullscreen';
    button.type = 'button';
    button.className = 'ghost-button traid-fullscreen-button';
    button.addEventListener('click', toggleFullscreen);
    const refresh = document.getElementById('refreshForecast');
    toolbar.insertBefore(button, refresh || null);
    updateFullscreenButton();
  }

  function installGlobalListeners() {
    document.addEventListener('fullscreenchange', () => {
      updateFullscreenButton();
      if (!document.fullscreenElement) applyChartRatio();
      scheduleChartResize();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && pseudoFullscreen) exitPseudoFullscreen();
    });
    DESKTOP_MEDIA.addEventListener?.('change', () => {
      applySidebarState();
      applyChartRatio();
    });
    window.addEventListener('resize', () => {
      if (!document.fullscreenElement && !pseudoFullscreen) applyChartRatio();
    }, { passive: true });

    const { workspace } = nodes();
    if (workspace && typeof ResizeObserver !== 'undefined') {
      let lastHeight = workspace.clientHeight;
      resizeObserver = new ResizeObserver(entries => {
        const height = Math.round(entries[0]?.contentRect?.height || 0);
        if (!height || Math.abs(height - lastHeight) < 2) return;
        lastHeight = height;
        applyChartRatio();
      });
      resizeObserver.observe(workspace);
    }
  }

  function initialize() {
    if (initialized) return;
    const { shell, workspace, chartView, analysisView, watchlist, trade, toolbar } = nodes();
    if (!shell || !workspace || !chartView || !analysisView || !watchlist || !trade || !toolbar) {
      setTimeout(initialize, 60);
      return;
    }
    initialized = true;
    installStyles();
    installSplitter();
    makeCollapseButton('markets');
    makeCollapseButton('execution');
    installFullscreenButton();
    installGlobalListeners();
    applySidebarState();
    applyChartRatio();
  }

  setTimeout(initialize, 0);
})();
