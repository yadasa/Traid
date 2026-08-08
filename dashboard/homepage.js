(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const nav = document.querySelector('.home-nav');
  const menu = document.getElementById('homeMenu');
  menu?.addEventListener('click', () => {
    const open = nav?.classList.toggle('open');
    menu.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  nav?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    nav.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
  }));

  function mulberry32(seed) {
    let value = seed >>> 0;
    return () => {
      value += 0x6D2B79F5;
      let t = value;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function makeCandles(count, start, seed, drift = 0) {
    const rand = mulberry32(seed);
    const rows = [];
    let close = start;
    for (let index = 0; index < count; index += 1) {
      const open = close;
      const impulse = (rand() - .48) * 3.8 + drift + Math.sin(index * .5) * .3;
      close = Math.max(5, open + impulse);
      const bodyTop = Math.max(open, close);
      const bodyBottom = Math.min(open, close);
      const high = bodyTop + .45 + rand() * 1.7;
      const low = bodyBottom - .45 - rand() * 1.7;
      rows.push({ open, high, low, close });
    }
    return rows;
  }

  function continueForecast(history, count, seed, direction = .25) {
    const last = history[history.length - 1];
    const rand = mulberry32(seed);
    const rows = [];
    let close = last.close;
    for (let index = 0; index < count; index += 1) {
      const open = close;
      const curve = Math.sin((index + 1) * .72) * .7;
      const impulse = direction + curve + (rand() - .5) * 2.2;
      close = open + impulse;
      const top = Math.max(open, close);
      const bottom = Math.min(open, close);
      rows.push({
        open,
        close,
        high: top + .45 + rand() * 1.25,
        low: bottom - .45 - rand() * 1.25,
      });
    }
    return rows;
  }

  function setupCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height };
  }

  function priceRange(rows) {
    let min = Infinity;
    let max = -Infinity;
    rows.forEach(row => {
      min = Math.min(min, row.low);
      max = Math.max(max, row.high);
    });
    const pad = Math.max(.8, (max - min) * .16);
    return { min: min - pad, max: max + pad };
  }

  function drawGrid(ctx, width, height, left, right, top, bottom) {
    ctx.save();
    ctx.strokeStyle = 'rgba(154,171,197,.085)';
    ctx.lineWidth = 1;
    const chartWidth = width - left - right;
    const chartHeight = height - top - bottom;
    for (let i = 0; i <= 5; i += 1) {
      const y = top + chartHeight * (i / 5);
      ctx.beginPath();
      ctx.moveTo(left, Math.round(y) + .5);
      ctx.lineTo(width - right, Math.round(y) + .5);
      ctx.stroke();
    }
    for (let i = 0; i <= 8; i += 1) {
      const x = left + chartWidth * (i / 8);
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + .5, top);
      ctx.lineTo(Math.round(x) + .5, height - bottom);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawCandle(ctx, x, candleWidth, row, yFor, palette, alpha = 1, glow = false) {
    const up = row.close >= row.open;
    const color = up ? palette.up : palette.down;
    const openY = yFor(row.open);
    const closeY = yFor(row.close);
    const highY = yFor(row.high);
    const lowY = yFor(row.low);
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(1.4, Math.abs(closeY - openY));

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    if (glow) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 13;
    }
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + .5, highY);
    ctx.lineTo(Math.round(x) + .5, lowY);
    ctx.stroke();
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    ctx.restore();
  }

  function drawLabel(ctx, text, x, y, align = 'left', color = '#697689') {
    ctx.save();
    ctx.fillStyle = color;
    ctx.font = '600 9px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = align;
    ctx.fillText(text, x, y);
    ctx.restore();
  }

  function drawForecastChart(canvas, options = {}) {
    const { ctx, width, height } = setupCanvas(canvas);
    const history = options.history;
    const forecast = options.forecast;
    const actual = options.actual || [];
    const reveal = Math.max(0, Math.min(actual.length, options.reveal ?? 0));
    const pulse = options.pulse ?? 0;
    const compact = width < 520;
    const left = compact ? 12 : 20;
    const right = compact ? 12 : 22;
    const top = 22;
    const bottom = 25;
    const rowsForScale = [...history, ...forecast, ...actual.slice(0, reveal)];
    const range = priceRange(rowsForScale);
    const chartHeight = height - top - bottom;
    const yFor = price => top + (range.max - price) / (range.max - range.min) * chartHeight;
    const totalSlots = history.length + forecast.length;
    const chartWidth = width - left - right;
    const slot = chartWidth / Math.max(1, totalSlots);
    const candleWidth = Math.max(2, Math.min(compact ? 6 : 8, slot * .56));
    const separatorSlot = history.length;
    const separatorX = left + slot * separatorSlot;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#080c13';
    ctx.fillRect(0, 0, width, height);
    drawGrid(ctx, width, height, left, right, top, bottom);

    const marketPalette = { up: '#35d1bc', down: '#ff667a' };
    const forecastPalette = { up: '#8ab4ff', down: '#aa8dff' };

    history.forEach((row, index) => {
      const x = left + slot * (index + .5);
      const isLive = index === history.length - 1;
      drawCandle(ctx, x, candleWidth, row, yFor, marketPalette, 1, isLive);
      if (isLive) {
        ctx.save();
        ctx.globalAlpha = .16 + pulse * .11;
        ctx.shadowColor = row.close >= row.open ? '#35d1bc' : '#b91c1c';
        ctx.shadowBlur = 22;
        drawCandle(ctx, x, candleWidth * 1.35, row, yFor, marketPalette, .5, true);
        ctx.restore();
      }
    });

    forecast.forEach((row, index) => {
      const x = left + slot * (history.length + index + .5);
      drawCandle(ctx, x, candleWidth, row, yFor, forecastPalette, .92, false);
    });

    if (actual.length) {
      actual.slice(0, reveal).forEach((row, index) => {
        const x = left + slot * (history.length + index + .5);
        const predicted = forecast[index];
        if (predicted) drawCandle(ctx, x, candleWidth + 3, predicted, yFor, forecastPalette, .17, false);
        drawCandle(ctx, x, Math.max(2, candleWidth - 1.4), row, yFor, marketPalette, 1, false);
      });
    }

    ctx.save();
    ctx.strokeStyle = 'rgba(151,126,255,.88)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    ctx.moveTo(separatorX, top);
    ctx.lineTo(separatorX, height - bottom);
    ctx.stroke();
    ctx.restore();

    drawLabel(ctx, 'REAL', separatorX - 8, top + 10, 'right', '#738196');
    drawLabel(ctx, 'FORECAST', separatorX + 8, top + 10, 'left', '#9d8bea');

    const lastPrice = history[history.length - 1]?.close;
    if (Number.isFinite(lastPrice)) {
      const y = yFor(lastPrice);
      ctx.save();
      ctx.strokeStyle = 'rgba(53,209,188,.28)';
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(width - right, y);
      ctx.stroke();
      ctx.restore();
    }
  }

  const heroCanvas = document.getElementById('heroChart');
  const replayCanvas = document.getElementById('replayChartDemo');
  const phoneCanvas = document.getElementById('phoneChartDemo');
  const heroHistory = makeCandles(34, 4042, 2104, .06);
  const heroForecast = continueForecast(heroHistory, 13, 88, .24);
  const replayHistory = makeCandles(31, 19214, 901, .18);
  const replayForecast = continueForecast(replayHistory, 11, 722, .34);
  const replayActual = continueForecast(replayHistory, 11, 1019, .23).map((row, index) => {
    const predicted = replayForecast[index];
    const blend = .57;
    const close = predicted.close * blend + row.close * (1 - blend);
    const open = index === 0 ? replayHistory[replayHistory.length - 1].close : (replayForecast[index - 1].close * blend + replayActualSafe(index - 1, row.open) * (1 - blend));
    const top = Math.max(open, close);
    const bottom = Math.min(open, close);
    return { open, close, high: Math.max(row.high, top + .35), low: Math.min(row.low, bottom - .35) };
  });

  function replayActualSafe(index, fallback) {
    if (index < 0) return fallback;
    const source = replayForecast[index];
    return source ? source.close + Math.sin(index * .8) * .45 : fallback;
  }

  const phoneHistory = makeCandles(22, 1.145, 2026, .002);
  const phoneForecast = continueForecast(phoneHistory, 8, 142, .025);

  let replayReveal = reducedMotion ? replayActual.length : 0;
  let lastRevealAt = performance.now();
  let frame = 0;

  function render(now) {
    const pulse = (Math.sin(now / 620) + 1) / 2;
    if (heroCanvas) drawForecastChart(heroCanvas, { history: heroHistory, forecast: heroForecast, pulse });
    if (phoneCanvas) drawForecastChart(phoneCanvas, { history: phoneHistory, forecast: phoneForecast, pulse });

    if (!reducedMotion && now - lastRevealAt > 760) {
      replayReveal += 1;
      if (replayReveal > replayActual.length + 2) replayReveal = 0;
      lastRevealAt = now;
    }
    const visibleReveal = Math.min(replayReveal, replayActual.length);
    if (replayCanvas) drawForecastChart(replayCanvas, {
      history: replayHistory,
      forecast: replayForecast,
      actual: replayActual,
      reveal: visibleReveal,
      pulse,
    });

    const revealNode = document.getElementById('replayRevealCount');
    if (revealNode) revealNode.textContent = `${String(visibleReveal).padStart(2, '0')} / ${String(replayActual.length).padStart(2, '0')}`;
    const dots = document.querySelectorAll('.replay-progress i');
    dots.forEach((dot, index) => dot.classList.toggle('active', index < Math.ceil((visibleReveal / replayActual.length) * dots.length)));

    const errNode = document.getElementById('demoCloseError');
    const atrNode = document.getElementById('demoAtrError');
    if (visibleReveal > 0) {
      const idx = visibleReveal - 1;
      const predicted = replayForecast[idx];
      const actual = replayActual[idx];
      const raw = Math.abs(predicted.close - actual.close);
      const pct = raw / Math.max(Math.abs(actual.close), 1e-9) * 100;
      const atr = 8.75;
      if (errNode) errNode.textContent = `${pct.toFixed(3)}%`;
      if (atrNode) atrNode.textContent = `${(raw / atr).toFixed(2)} ATR`;
    } else {
      if (errNode) errNode.textContent = '—';
      if (atrNode) atrNode.textContent = '—';
    }

    if (!reducedMotion) frame = requestAnimationFrame(render);
  }

  const redraw = () => {
    if (reducedMotion) render(performance.now());
  };
  new ResizeObserver(redraw).observe(document.body);
  render(performance.now());

  window.addEventListener('pagehide', () => cancelAnimationFrame(frame), { once: true });
})();
