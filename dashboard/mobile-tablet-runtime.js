(() => {
  const ROOT = document.documentElement;
  const BODY = document.body;
  const compactMedia = window.matchMedia('(pointer: coarse)');
  let lastMode = '';
  let resizeFrame = 0;

  function viewportWidth() {
    return Math.round(window.visualViewport?.width || window.innerWidth || ROOT.clientWidth || 0);
  }

  function viewportHeight() {
    return Math.round(window.visualViewport?.height || window.innerHeight || ROOT.clientHeight || 0);
  }

  function classify() {
    const width = viewportWidth();
    const height = viewportHeight();
    const coarse = compactMedia.matches || Number(navigator.maxTouchPoints || 0) > 0;
    const phone = width <= 720;
    const tablet = !phone && coarse && width <= 1366;
    const compact = phone || tablet;
    const landscape = width > height;
    const mode = phone ? 'phone' : tablet ? 'tablet' : 'desktop';

    BODY.classList.toggle('traid-phone', phone);
    BODY.classList.toggle('traid-tablet', tablet);
    BODY.classList.toggle('traid-touch-layout', compact);
    BODY.classList.toggle('traid-landscape', compact && landscape);
    BODY.classList.toggle('traid-portrait', compact && !landscape);
    ROOT.style.setProperty('--traid-app-height', `${Math.max(320, height)}px`);
    ROOT.style.setProperty('--traid-app-width', `${Math.max(320, width)}px`);

    if (compact) {
      const workspace = document.querySelector('.workspace');
      workspace?.style.removeProperty('grid-template-rows');
      workspace?.style.removeProperty('gap');
    }

    if (mode !== lastMode) {
      lastMode = mode;
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }
  }

  function scheduleClassify() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(classify);
  }

  function installStyles() {
    if (document.getElementById('traidMobileTabletStyles')) return;
    const style = document.createElement('style');
    style.id = 'traidMobileTabletStyles';
    style.textContent = `
      body.traid-touch-layout {
        overflow:hidden !important;
        overscroll-behavior:none;
        -webkit-text-size-adjust:100%;
      }
      body.traid-touch-layout .mobile-only { display:grid !important; }
      body.traid-touch-layout .app-shell {
        width:100%;
        height:var(--traid-app-height,100dvh) !important;
        min-height:0 !important;
        grid-template-columns:1fr !important;
        grid-template-areas:"top" "work" "nav" !important;
        gap:0 !important;
        padding:0 !important;
      }
      body.traid-touch-layout .topbar {
        grid-area:top;
        margin:0 !important;
        position:relative !important;
        top:auto !important;
        width:100%;
        min-width:0;
        border-radius:0;
      }
      body.traid-touch-layout .workspace {
        grid-area:work;
        display:block !important;
        min-width:0;
        min-height:0;
        height:100%;
        overflow:hidden;
      }
      body.traid-touch-layout .chart-card,
      body.traid-touch-layout .lower-panel {
        width:100%;
        height:100% !important;
        min-height:0 !important;
        border-left:0 !important;
        border-right:0 !important;
        border-radius:0 !important;
        box-shadow:none;
      }
      body.traid-touch-layout .lower-panel {
        display:none !important;
        grid-template-rows:50px minmax(0,1fr) !important;
      }
      body.traid-touch-layout .workspace.show-analysis .chart-card { display:none !important; }
      body.traid-touch-layout .workspace.show-analysis .lower-panel { display:grid !important; }
      body.traid-touch-layout .chart-toolbar {
        min-width:0;
        overflow-x:auto !important;
        overflow-y:hidden !important;
        scrollbar-width:none;
        overscroll-behavior-x:contain;
        -webkit-overflow-scrolling:touch;
        scroll-padding-inline:10px;
      }
      body.traid-touch-layout .chart-toolbar::-webkit-scrollbar,
      body.traid-touch-layout .tab-bar::-webkit-scrollbar { display:none; }
      body.traid-touch-layout .chart-toolbar > * { flex:0 0 auto; }
      body.traid-touch-layout .chart-toolbar #refreshForecast { display:none !important; }
      body.traid-touch-layout .chart-toolbar #traidChartFullscreen,
      body.traid-touch-layout .chart-toolbar #chartTimeModeButton {
        display:grid !important;
        flex:0 0 40px !important;
        width:40px !important;
        min-width:40px !important;
        height:40px !important;
        padding:0 !important;
        place-items:center;
      }
      body.traid-touch-layout .chart-toolbar #chartTimeModeButton {
        width:auto !important;
        min-width:70px !important;
        padding:0 10px !important;
      }
      body.traid-touch-layout .select-control,
      body.traid-touch-layout input,
      body.traid-touch-layout select,
      body.traid-touch-layout textarea {
        font-size:16px;
      }
      body.traid-touch-layout .select-control,
      body.traid-touch-layout input,
      body.traid-touch-layout select { min-height:44px; }
      body.traid-touch-layout .ghost-button,
      body.traid-touch-layout .primary-button,
      body.traid-touch-layout .danger-outline,
      body.traid-touch-layout .icon-button { min-height:44px; }
      body.traid-touch-layout button:hover:not(:disabled) { transform:none; }
      body.traid-touch-layout .segmented { padding:3px; }
      body.traid-touch-layout .segmented button {
        min-height:36px;
        min-width:42px;
        padding:0 10px;
        font-size:10px;
      }
      body.traid-touch-layout .switch-control { min-height:40px; }
      body.traid-touch-layout .chart-status-row { min-width:0; }
      body.traid-touch-layout .legend {
        min-width:0;
        overflow:hidden;
        text-overflow:ellipsis;
      }
      body.traid-touch-layout .chart-wrap { min-height:0 !important; }
      body.traid-touch-layout .advanced-strip {
        display:flex !important;
        grid-template-columns:none !important;
        min-height:0;
        max-height:84px;
        overflow-x:auto !important;
        overflow-y:hidden !important;
        scrollbar-width:none;
        overscroll-behavior-x:contain;
        -webkit-overflow-scrolling:touch;
      }
      body.traid-touch-layout .advanced-strip::-webkit-scrollbar { display:none; }
      body.traid-touch-layout .advanced-strip article,
      body.traid-touch-layout .advanced-strip article:nth-child(n) {
        display:block !important;
        flex:0 0 168px;
        min-width:168px;
        border-right:1px solid var(--line) !important;
      }
      body.traid-touch-layout .tab-bar {
        min-width:0;
        height:50px;
        padding:5px 8px;
        gap:4px;
        overflow-x:auto;
        overflow-y:hidden;
        scrollbar-width:none;
        overscroll-behavior-x:contain;
        -webkit-overflow-scrolling:touch;
      }
      body.traid-touch-layout .tab-bar button {
        min-width:88px;
        min-height:40px;
        padding:0 14px;
        font-size:10px;
        flex:0 0 auto;
      }
      body.traid-touch-layout .tab-content {
        min-height:0;
        overscroll-behavior:contain;
        -webkit-overflow-scrolling:touch;
      }
      body.traid-touch-layout .table-scroll {
        width:100%;
        overflow:auto;
        overscroll-behavior-x:contain;
        -webkit-overflow-scrolling:touch;
        border-radius:9px;
      }
      body.traid-touch-layout table { min-width:max-content; font-size:10px; }
      body.traid-touch-layout th,
      body.traid-touch-layout td { height:42px; padding:0 11px; }
      body.traid-touch-layout .table-scroll th:first-child,
      body.traid-touch-layout .table-scroll td:first-child {
        position:sticky;
        left:0;
        z-index:2;
        background:#0a1024;
        box-shadow:1px 0 0 rgba(139,158,213,.10);
      }
      body.traid-touch-layout .content-heading { align-items:flex-start; }
      body.traid-touch-layout .content-heading h3 { font-size:15px; }
      body.traid-touch-layout .content-heading p { font-size:11px; line-height:1.45; }
      body.traid-touch-layout .stat-grid {
        grid-template-columns:repeat(auto-fit,minmax(145px,1fr)) !important;
        gap:9px;
      }
      body.traid-touch-layout .stat-grid article { min-height:76px; padding:13px; }
      body.traid-touch-layout .stat-grid span { font-size:8px; }
      body.traid-touch-layout .stat-grid strong { font-size:17px; }
      body.traid-touch-layout .list-row { min-height:42px; padding:9px 11px; font-size:10px; }
      body.traid-touch-layout .timeline-item { min-height:54px; }
      body.traid-touch-layout .journal-card { padding:13px; }
      body.traid-touch-layout .drawer,
      body.traid-touch-layout .watchlist,
      body.traid-touch-layout .trade-panel {
        -webkit-overflow-scrolling:touch;
        overscroll-behavior:contain;
      }
      body.traid-touch-layout .modal-backdrop {
        position:fixed;
        inset:0;
        z-index:70;
      }
      body.traid-touch-layout .mobile-nav {
        grid-area:nav;
        display:grid !important;
        grid-template-columns:repeat(5,1fr);
        width:100%;
        min-width:0;
        border-top:1px solid var(--line);
        background:rgba(5,7,19,.965);
        backdrop-filter:blur(24px);
        z-index:75;
      }
      body.traid-touch-layout .mobile-nav button {
        min-width:0;
        border:0;
        background:transparent;
        color:var(--muted);
        display:grid;
        place-items:center;
        align-content:center;
        gap:3px;
        font-weight:800;
      }
      body.traid-touch-layout .mobile-nav button.active {
        color:var(--blue-2);
        background:linear-gradient(180deg,rgba(59,130,246,.03),rgba(59,130,246,.11));
      }
      body.traid-touch-layout .traid-side-collapse,
      body.traid-touch-layout .traid-chart-splitter { display:none !important; }

      /* iPad / touch-tablet layout */
      body.traid-tablet .app-shell {
        grid-template-rows:64px minmax(0,1fr) calc(64px + env(safe-area-inset-bottom)) !important;
      }
      body.traid-tablet .topbar {
        padding:max(8px,env(safe-area-inset-top)) 16px 7px !important;
        gap:12px !important;
      }
      body.traid-tablet .brand { min-width:120px; }
      body.traid-tablet .brand-mark { width:34px; height:34px; border-radius:10px; }
      body.traid-tablet .brand-copy strong { font-size:13px; }
      body.traid-tablet .brand-copy small { font-size:8px; }
      body.traid-tablet .market-head { flex:1; overflow:hidden; }
      body.traid-tablet .live-price { font-size:21px; }
      body.traid-tablet #tradingPill,
      body.traid-tablet #notificationButton { display:none !important; }
      body.traid-tablet .top-actions { gap:6px; }
      body.traid-tablet .top-actions .pill { display:inline-flex; }
      body.traid-tablet .chart-card {
        grid-template-rows:56px 30px minmax(0,1fr) auto !important;
      }
      body.traid-tablet .chart-toolbar { padding:7px 12px !important; gap:7px !important; }
      body.traid-tablet .select-control { min-width:112px; height:40px; font-size:12px; }
      body.traid-tablet .chart-status-row { padding:0 12px; font-size:9px; }
      body.traid-tablet .legend { max-width:74%; gap:9px; }
      body.traid-tablet .legend span:nth-child(n) { display:inline-flex; }
      body.traid-tablet .legend span:nth-child(n+4) { display:none; }
      body.traid-tablet .forecast-label { top:10px; right:10px; font-size:8px; }
      body.traid-tablet .chart-watermark { left:16px; top:16px; }
      body.traid-tablet .lower-panel { grid-template-rows:52px minmax(0,1fr) !important; }
      body.traid-tablet .tab-content { padding:16px !important; }
      body.traid-tablet .forecast-detail-grid { gap:14px; }
      body.traid-tablet .watchlist {
        position:fixed !important;
        inset:0 auto 0 0 !important;
        width:min(360px,46vw) !important;
        height:var(--traid-app-height,100dvh) !important;
        z-index:90 !important;
        border-radius:0 16px 16px 0 !important;
        transform:translateX(-105%) !important;
        transition:transform 190ms cubic-bezier(.2,.8,.2,1) !important;
      }
      body.traid-tablet .watchlist.open { transform:translateX(0) !important; }
      body.traid-tablet .trade-panel {
        position:fixed !important;
        top:max(10px,env(safe-area-inset-top)) !important;
        right:10px !important;
        bottom:calc(72px + env(safe-area-inset-bottom)) !important;
        left:auto !important;
        width:min(460px,58vw) !important;
        height:auto !important;
        min-height:0 !important;
        z-index:85 !important;
        display:flex !important;
        flex-direction:column !important;
        border-radius:16px !important;
        padding-bottom:8px !important;
        transform:translateX(calc(100% + 24px)) !important;
        transition:transform 210ms cubic-bezier(.2,.8,.2,1) !important;
        overflow-y:auto !important;
      }
      body.traid-tablet .trade-panel.open { transform:translateX(0) !important; }
      body.traid-tablet .trade-handle { display:none !important; }
      body.traid-tablet .trade-form { grid-template-columns:1fr 1fr !important; }
      body.traid-tablet .drawer {
        width:min(440px,62vw) !important;
        padding:max(18px,env(safe-area-inset-top)) 18px max(18px,env(safe-area-inset-bottom)) !important;
      }
      body.traid-tablet .mobile-nav {
        padding:5px 12px max(5px,env(safe-area-inset-bottom)) !important;
      }
      body.traid-tablet .mobile-nav button { height:52px; font-size:9px; border-radius:10px; }
      body.traid-tablet .mobile-nav button span { font-size:18px; line-height:1; }
      body.traid-tablet .historical-replay-controls {
        grid-template-columns:minmax(0,1.4fr) minmax(150px,.6fr) !important;
        gap:10px !important;
      }
      body.traid-tablet .historical-replay-controls .historical-replay-pick-button,
      body.traid-tablet .historical-replay-controls .primary-button {
        grid-column:auto !important;
        min-height:44px !important;
      }
      body.traid-tablet .historical-replay-transport { gap:8px !important; }
      body.traid-tablet .historical-replay-progress {
        width:100% !important;
        max-width:none !important;
        margin-left:0 !important;
      }
      body.traid-tablet .historical-replay-chart-wrap {
        height:clamp(300px,46vh,440px) !important;
      }

      /* Phone layout */
      body.traid-phone .app-shell {
        grid-template-rows:calc(56px + env(safe-area-inset-top)) minmax(0,1fr) calc(60px + env(safe-area-inset-bottom)) !important;
      }
      body.traid-phone .topbar {
        padding:max(7px,env(safe-area-inset-top)) 9px 6px !important;
        gap:8px !important;
      }
      body.traid-phone .brand { min-width:auto !important; }
      body.traid-phone .brand-copy { display:none !important; }
      body.traid-phone .brand-mark { width:31px; height:31px; border-radius:9px; }
      body.traid-phone .market-head { flex:1; gap:6px; overflow:hidden; }
      body.traid-phone .connection-dot { width:6px; height:6px; }
      body.traid-phone .live-price { font-size:17px; }
      body.traid-phone .price-change { font-size:8px; max-width:84px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      body.traid-phone .top-actions .pill,
      body.traid-phone #notificationButton { display:none !important; }
      body.traid-phone .top-actions { margin-left:0; gap:5px; }
      body.traid-phone .icon-button { width:40px; min-width:40px; height:40px; }
      body.traid-phone .chart-card {
        grid-template-rows:50px 26px minmax(0,1fr) auto !important;
      }
      body.traid-phone .chart-toolbar { padding:5px 7px !important; gap:5px !important; }
      body.traid-phone .select-control { min-width:92px; height:40px; font-size:12px; }
      body.traid-phone .segmented button { min-width:38px; height:36px; padding:0 8px; }
      body.traid-phone .chart-toolbar .switch-control { min-width:75px; }
      body.traid-phone .chart-status-row { padding:0 8px; font-size:8px; gap:7px; }
      body.traid-phone .legend { max-width:60%; gap:7px; }
      body.traid-phone .legend span:nth-child(n+3) { display:none !important; }
      body.traid-phone #forecastStatus { max-width:40%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      body.traid-phone .forecast-label { top:7px; right:7px; padding:5px 6px; font-size:6.5px; }
      body.traid-phone .chart-watermark { left:10px; top:10px; }
      body.traid-phone .chart-watermark span { font-size:17px; }
      body.traid-phone .advanced-strip { max-height:72px; }
      body.traid-phone .advanced-strip article,
      body.traid-phone .advanced-strip article:nth-child(n) {
        flex-basis:150px;
        min-width:150px;
        padding:8px 9px !important;
      }
      body.traid-phone .lower-panel { grid-template-rows:48px minmax(0,1fr) !important; }
      body.traid-phone .tab-bar { height:48px; padding:4px 6px; }
      body.traid-phone .tab-bar button { min-width:82px; min-height:38px; padding:0 11px; }
      body.traid-phone .tab-content { padding:11px !important; }
      body.traid-phone .content-heading { gap:8px; }
      body.traid-phone .content-heading > div:last-child { flex-wrap:wrap; justify-content:flex-end; }
      body.traid-phone .stat-grid { grid-template-columns:repeat(2,minmax(0,1fr)) !important; }
      body.traid-phone .forecast-detail-grid { grid-template-columns:1fr !important; gap:12px; }
      body.traid-phone .timeline-item { grid-template-columns:72px minmax(0,1fr) !important; gap:8px; }
      body.traid-phone .timeline-item .impact { grid-column:2; justify-self:start; }
      body.traid-phone .journal-grid { grid-template-columns:1fr !important; }
      body.traid-phone .watchlist {
        position:fixed !important;
        inset:0 auto 0 0 !important;
        width:min(330px,90vw) !important;
        height:var(--traid-app-height,100dvh) !important;
        z-index:90 !important;
        border-radius:0 16px 16px 0 !important;
        transform:translateX(-105%) !important;
        transition:transform 190ms ease !important;
      }
      body.traid-phone .watchlist.open { transform:translateX(0) !important; }
      body.traid-phone .trade-panel {
        position:fixed !important;
        left:0 !important;
        right:0 !important;
        top:max(7vh,env(safe-area-inset-top)) !important;
        bottom:calc(58px + env(safe-area-inset-bottom)) !important;
        width:100% !important;
        height:auto !important;
        min-height:0 !important;
        z-index:85 !important;
        display:flex !important;
        flex-direction:column !important;
        overflow-y:auto !important;
        border-radius:18px 18px 0 0 !important;
        padding-bottom:max(10px,env(safe-area-inset-bottom)) !important;
        transform:translateY(110%) !important;
        transition:transform 210ms cubic-bezier(.2,.8,.2,1) !important;
      }
      body.traid-phone .trade-panel.open { transform:translateY(0) !important; }
      body.traid-phone .trade-form { grid-template-columns:1fr 1fr !important; }
      body.traid-phone .trade-form input,
      body.traid-phone .trade-form select { width:100%; }
      body.traid-phone .drawer {
        width:100% !important;
        padding:max(14px,env(safe-area-inset-top)) 14px max(20px,env(safe-area-inset-bottom)) !important;
      }
      body.traid-phone .mobile-nav {
        padding:4px 3px max(4px,env(safe-area-inset-bottom)) !important;
      }
      body.traid-phone .mobile-nav button { height:50px; font-size:8px; border-radius:8px; }
      body.traid-phone .mobile-nav button span { font-size:17px; line-height:1; }
      body.traid-phone .historical-replay-controls {
        grid-template-columns:1fr !important;
        padding:11px !important;
        gap:9px !important;
      }
      body.traid-phone .historical-replay-controls .historical-replay-pick-button,
      body.traid-phone .historical-replay-controls .primary-button {
        grid-column:1 !important;
        width:100% !important;
        min-height:44px !important;
      }
      body.traid-phone .historical-replay-transport {
        padding:10px !important;
        gap:7px !important;
      }
      body.traid-phone .historical-replay-transport .ghost-button {
        flex:1 1 calc(33.333% - 7px);
        min-width:82px !important;
      }
      body.traid-phone .historical-replay-speed { width:100% !important; }
      body.traid-phone .historical-replay-progress {
        width:100% !important;
        max-width:none !important;
        margin-left:0 !important;
      }
      body.traid-phone .historical-replay-error-card { padding-right:94px !important; }
      body.traid-phone .historical-replay-chart-wrap {
        height:clamp(280px,42vh,380px) !important;
        min-height:280px !important;
      }
      body.traid-phone .historical-replay-copy h3 { font-size:16px !important; }
      body.traid-phone .historical-replay-copy p { font-size:10.5px !important; }
      body.traid-phone .historical-replay-legend { max-width:calc(100% - 24px); overflow-x:auto; }

      @media (max-width:430px) {
        body.traid-phone .price-change { display:none !important; }
        body.traid-phone .chart-toolbar .switch-control span:last-child { display:none !important; }
        body.traid-phone .chart-toolbar .switch-control { min-width:42px !important; }
        body.traid-phone .trade-form { grid-template-columns:1fr !important; }
        body.traid-phone .trade-form .full { grid-column:1 !important; }
        body.traid-phone .risk-preview { grid-template-columns:1fr 1fr !important; }
        body.traid-phone .risk-preview span:last-child { grid-column:1/-1; }
        body.traid-phone .content-heading { flex-direction:column; align-items:stretch; }
        body.traid-phone .content-heading > div:last-child { justify-content:flex-start; }
      }

      @media (orientation:landscape) and (max-height:520px) {
        body.traid-phone .app-shell {
          grid-template-rows:calc(48px + env(safe-area-inset-top)) minmax(0,1fr) calc(52px + env(safe-area-inset-bottom)) !important;
        }
        body.traid-phone .topbar { padding:max(4px,env(safe-area-inset-top)) 8px 3px !important; }
        body.traid-phone .brand-mark { width:29px; height:29px; }
        body.traid-phone .live-price { font-size:16px; }
        body.traid-phone .chart-card { grid-template-rows:46px 22px minmax(0,1fr) auto !important; }
        body.traid-phone .chart-status-row { font-size:7px; }
        body.traid-phone .advanced-strip { max-height:56px; }
        body.traid-phone .advanced-strip article,
        body.traid-phone .advanced-strip article:nth-child(n) { flex-basis:140px; min-width:140px; padding:6px 8px !important; }
        body.traid-phone .mobile-nav button { height:42px; }
        body.traid-phone .trade-panel { top:env(safe-area-inset-top) !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function installTouchDismissals() {
    const backdrop = document.getElementById('modalBackdrop');
    if (!backdrop || backdrop.dataset.traidTouchDismissal === 'true') return;
    backdrop.dataset.traidTouchDismissal = 'true';
    backdrop.addEventListener('click', () => {
      document.getElementById('watchlistPanel')?.classList.remove('open');
      document.getElementById('tradePanel')?.classList.remove('open');
      document.getElementById('settingsDrawer')?.classList.remove('open');
      document.getElementById('settingsDrawer')?.setAttribute('aria-hidden', 'true');
      backdrop.classList.add('hidden');
    });
  }

  function initialize() {
    installStyles();
    installTouchDismissals();
    classify();
    window.addEventListener('resize', scheduleClassify, { passive: true });
    window.addEventListener('orientationchange', scheduleClassify, { passive: true });
    compactMedia.addEventListener?.('change', scheduleClassify);
    window.visualViewport?.addEventListener('resize', scheduleClassify, { passive: true });
    window.visualViewport?.addEventListener('scroll', scheduleClassify, { passive: true });
  }

  initialize();
})();
