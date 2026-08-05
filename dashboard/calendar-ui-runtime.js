function calendarAffectedSymbols(event) {
  const symbols = event?.metadata?.affected_symbols;
  return Array.isArray(symbols) ? symbols : [];
}

function calendarCountdown(startsAt) {
  const milliseconds = new Date(startsAt).getTime() - Date.now();
  if (!Number.isFinite(milliseconds)) return '';
  const minutes = Math.round(milliseconds / 60000);
  if (minutes <= 0) return 'now';
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `in ${hours}h ${remainder}m` : `in ${hours}h`;
}

async function loadCalendar() {
  try {
    const symbol = state.symbol;
    const now = new Date();
    const end = new Date(Date.now() + 7 * 86400000);
    const payload = await api(
      `/v1/calendar/live?symbol=${encodeURIComponent(symbol)}&start=${encodeURIComponent(now.toISOString())}&end=${encodeURIComponent(end.toISOString())}`
    );
    if (symbol !== state.symbol) return;

    const events = Array.isArray(payload.events) ? payload.events : [];
    $('calendarList').innerHTML = events.map(event => {
      const affected = calendarAffectedSymbols(event);
      const affectedText = affected.length ? `Affects ${affected.join(', ')}` : `Affects ${event.currency}`;
      return `<article class="timeline-item">
        <time>${new Date(event.starts_at).toLocaleString([], { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' })}</time>
        <div>
          <strong>${escapeHtml(event.title)}</strong>
          <small>${escapeHtml(event.currency)} · ${escapeHtml(affectedText)} · ${escapeHtml(event.source || 'Economic calendar')}</small>
        </div>
        <span class="impact ${escapeHtml(event.impact)}">${escapeHtml(event.impact)}</span>
      </article>`;
    }).join('') || '<div class="empty-cell">No medium- or high-impact events currently affect this symbol.</div>';

    const next = events[0];
    $('nextEvent').innerHTML = next
      ? `<strong>${escapeHtml(next.title)}</strong><small>${new Date(next.starts_at).toLocaleString()} · ${escapeHtml(String(next.impact || '').toUpperCase())} · ${escapeHtml(calendarCountdown(next.starts_at))} · Affects ${escapeHtml(calendarAffectedSymbols(next).join(', ') || next.currency)}</small>`
      : payload.refresh_error
        ? `<strong>Calendar temporarily unavailable</strong><small>${escapeHtml(payload.refresh_error)}</small>`
        : '<strong>No major event ahead</strong><small>No medium- or high-impact event currently affects this symbol.</small>';
  } catch (error) {
    console.warn(error);
    $('nextEvent').innerHTML = '<strong>Calendar temporarily unavailable</strong><small>The cached event list could not be loaded.</small>';
  }
}
