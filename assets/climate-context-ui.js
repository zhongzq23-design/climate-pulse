'use strict';

// Same-month historical normals vs a recent seven-day mean from complete IFS daily windows.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  const COLORS = { tmp: '#c94f3d', pre: '#2e7aa5', vpd: '#c8871b' };
  const META = {
    tmp: { label: 'Temperature', sentenceLabel: 'temperature', unit: '°C' },
    pre: { label: 'Precipitation', sentenceLabel: 'precipitation', unit: 'mm/day' },
    vpd: { label: 'VPD', sentenceLabel: 'VPD', unit: 'hPa' }
  };

  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const html = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
  const signed = (v, digits = 1) => finite(v) ? `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}` : '–';
  const fmt = (variable, value) => {
    if (!finite(value)) return '–';
    const n = Number(value);
    if (variable === 'tmp') return `${n.toFixed(1)} °C`;
    if (variable === 'vpd') return `${n.toFixed(2)} hPa`;
    return `${n.toFixed(n < 10 ? 2 : 1)} mm/day`;
  };
  const getPeriod = (ctx, key) => (ctx.comparison?.periods || []).find(p => p.key === key) || null;
  const getRecent = ctx => getPeriod(ctx, 'recent_7d') || getPeriod(ctx, 'current_24h');
  const safeEventId = id => String(id || 'event').replace(/[^A-Za-z0-9_.-]+/g, '_').slice(0, 160);
  const fallbackPathForEvent = event => `data/climate/event_timeseries/${safeEventId(event?.id)}.json`;
  const compactUtc = iso => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })} ${String(d.getUTCHours()).padStart(2, '0')}Z`;
  };

  function deltaText(variable, recent, modern) {
    if (!finite(recent) || !finite(modern)) return 'Recent comparison unavailable';
    const c = Number(recent), m = Number(modern);
    if (variable === 'pre') {
      if (Math.abs(m) < 1e-9) return `${signed(c - m, 2)} mm/day vs 1981–2010`;
      const pct = (c - m) / m * 100;
      return `${signed(pct, 0)}% vs 1981–2010`;
    }
    return `${signed(c - m, variable === 'tmp' ? 1 : 2)} ${META[variable].unit} vs 1981–2010`;
  }

  function comparisonSentence(variable, recent, modern, monthLabel) {
    if (!finite(recent) || !finite(modern)) return 'Recent comparison is unavailable for this variable.';
    const c = Number(recent), m = Number(modern);
    const normalName = `the 1981–2010 ${monthLabel || 'same-month'} normal`;
    if (variable === 'pre') {
      if (Math.abs(m) < 1e-9) return `Recent 7-day precipitation is ${fmt(variable, c)}; ${normalName} is near zero.`;
      const pct = (c - m) / m * 100;
      if (Math.abs(pct) < 5) return `Recent 7-day precipitation is close to ${normalName} (${signed(pct, 0)}%).`;
      return `Recent 7-day precipitation is ${Math.abs(pct).toFixed(0)}% ${pct > 0 ? 'higher' : 'lower'} than ${normalName}.`;
    }
    const diff = c - m;
    const digits = variable === 'tmp' ? 1 : 2;
    if (Math.abs(diff) < (variable === 'tmp' ? 0.05 : 0.005)) {
      return `Recent 7-day ${META[variable].sentenceLabel} is close to ${normalName}.`;
    }
    return `Recent 7-day ${META[variable].sentenceLabel} is ${Math.abs(diff).toFixed(digits)} ${META[variable].unit} ${diff > 0 ? 'above' : 'below'} ${normalName}.`;
  }

  function periodCell(variable, period, monthLabel) {
    if (!period) return '<div class="comparison-period is-missing"><div class="comparison-period-label">Unavailable</div><div class="comparison-value">–</div></div>';
    const value = period.values?.[variable];
    const historical = period.kind === 'same_calendar_month_climatology';
    const subtitle = historical
      ? `${monthLabel} mean · ${period.n_years || 30} years`
      : period.status === 'ready'
        ? `${period.day_count || 7} days · ${compactUtc(period.window_start)} → ${compactUtc(period.window_end)}`
        : (period.reason || 'Earth Engine NRT unavailable');
    return `<div class="comparison-period${period.status === 'unavailable' ? ' is-missing' : ''}"><div class="comparison-period-label">${html(period.label)}</div><div class="comparison-value">${html(fmt(variable, value))}</div><div class="comparison-period-sub">${html(subtitle)}</div></div>`;
  }

  function miniBarChart(variable, early, modern, recent) {
    const rows = [
      { key: 'early', label: '1901–30', period: early },
      { key: 'modern', label: '1981–10', period: modern },
      { key: 'recent', label: 'Recent 7d', period: recent }
    ];
    const values = rows.map(row => row.period?.values?.[variable]);
    const numeric = values.filter(finite).map(Number);
    if (!numeric.length) return '<div class="comparison-chart-empty">No chart data available</div>';

    let min = Math.min(0, ...numeric);
    let max = Math.max(0, ...numeric);
    if (Math.abs(max - min) < 1e-12) {
      max += 1;
      min -= 1;
    }
    const top = 10;
    const bottom = 82;
    const plotHeight = bottom - top;
    const y = value => top + (max - value) / (max - min) * plotHeight;
    const zeroY = y(0);
    const xPositions = [38, 126, 214];
    const width = 46;
    const bars = rows.map((row, index) => {
      const value = values[index];
      const x = xPositions[index];
      const cx = x + width / 2;
      if (!finite(value)) {
        return `<g class="climate-mini-group is-missing"><text x="${cx}" y="48" text-anchor="middle" class="climate-mini-missing">–</text><text x="${cx}" y="108" text-anchor="middle" class="climate-mini-label">${html(row.label)}</text></g>`;
      }
      const valueY = y(Number(value));
      const rectY = Math.min(valueY, zeroY);
      const height = Math.max(2, Math.abs(zeroY - valueY));
      return `<g class="climate-mini-group is-${row.key}"><rect x="${x}" y="${rectY.toFixed(2)}" width="${width}" height="${height.toFixed(2)}" rx="7" class="climate-mini-bar"></rect><text x="${cx}" y="108" text-anchor="middle" class="climate-mini-label">${html(row.label)}</text></g>`;
    }).join('');
    const aria = `${META[variable].label}: 1901–1930 ${fmt(variable, values[0])}; 1981–2010 ${fmt(variable, values[1])}; recent 7-day ${fmt(variable, values[2])}.`;
    return `<div class="comparison-chart" style="color:${COLORS[variable]}"><svg viewBox="0 0 300 116" role="img" aria-label="${html(aria)}" preserveAspectRatio="xMidYMid meet"><line x1="14" y1="${zeroY.toFixed(2)}" x2="286" y2="${zeroY.toFixed(2)}" class="climate-mini-zero"></line>${bars}</svg></div>`;
  }

  function variableCard(variable, ctx) {
    const monthLabel = ctx.comparison?.month_label || '';
    const early = getPeriod(ctx, '1901_1930');
    const modern = getPeriod(ctx, '1981_2010');
    const recent = getRecent(ctx);
    const recentValue = recent?.values?.[variable];
    const modernValue = modern?.values?.[variable];
    const delta = deltaText(variable, recentValue, modernValue);
    const summary = comparisonSentence(variable, recentValue, modernValue, monthLabel);
    return `<article class="comparison-card"><div class="comparison-card-head"><div class="comparison-variable"><span class="comparison-dot" style="background:${COLORS[variable]}"></span><div><h4>${html(META[variable].label)}</h4><div class="comparison-delta">${html(delta)}</div></div></div><p class="comparison-summary">${html(summary)}</p></div><div class="comparison-card-body"><div class="comparison-chart-wrap">${miniBarChart(variable, early, modern, recent)}</div><div class="comparison-periods">${periodCell(variable, early, monthLabel)}${periodCell(variable, modern, monthLabel)}${periodCell(variable, recent, monthLabel)}</div></div></article>`;
  }

  async function fetchContext(path) {
    if (CACHE.has(path)) return CACHE.get(path);
    const candidates = [path, `https://raw.githubusercontent.com/zhongzq23-design/climate-pulse/main/${path}`];
    let last = null;
    for (const url of candidates) {
      try {
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        CACHE.set(path, data);
        return data;
      } catch (err) { last = err; }
    }
    throw last || new Error('Climate context unavailable');
  }

  function removeSideClimateHint() {
    const panel = document.getElementById('detailPanel');
    if (!panel) return;
    const label = [...panel.querySelectorAll('.section-label')].find(x => /local warming context|climate background/i.test(x.textContent || ''));
    if (!label) return;
    const box = label.nextElementSibling;
    if (box?.classList.contains('climate-box')) box.remove();
    label.remove();
  }

  function legacyMessage() {
    return `<div class="climate-status">This event still has the previous climate-context schema. It will switch to the recent 7-day comparison on the next backend refresh.</div>`;
  }

  function renderReady(ctx, event) {
    if (!ctx.comparison?.periods) return legacyMessage();
    const vars = Array.isArray(ctx.variable_profile) ? ctx.variable_profile : (event.climate_context?.variables || ['tmp', 'pre']);
    const monthLabel = ctx.comparison.month_label || '';
    const recent = getRecent(ctx);
    const isV2 = recent?.key === 'recent_7d';
    const location = ctx.comparison_location || {};
    const method = location.selection_method === 'nearest_valid_land_cell' ? 'nearest valid CRU land cell' : 'nearest CRU grid cell';
    const recentMeta = recent?.status === 'ready'
      ? `IFS 0.25° · ${isV2 ? '7 complete days' : 'complete 24h'} ending ${html(compactUtc(recent.window_end))}`
      : 'IFS recent window pending';
    const cards = vars.filter(v => META[v]).map(v => variableCard(v, ctx)).join('');
    const windowNote = recent?.status === 'ready'
      ? isV2
        ? `Recent values average seven consecutive fully elapsed IFS 0–24 h forecast windows (${compactUtc(recent.window_start)} → ${compactUtc(recent.window_end)}). Precipitation is the seven-day total divided by 7.`
        : `Current 24h uses the latest fully elapsed IFS 0–24 h forecast window (${compactUtc(recent.window_start)} → ${compactUtc(recent.window_end)}).`
      : `Historical normals are ready; the recent IFS comparison is temporarily unavailable.`;
    return `<div class="climate-inline-head"><div><h3>Climate comparison · ${html(monthLabel)}</h3><p>Each chart compares two same-month climate normals with the recent 7-day mean; exact values and a short interpretation are shown alongside.</p></div><div class="climate-context-meta">CRU-TS v4.10 · 0.5°<br>${html(recentMeta)}<br>${html(method)}${finite(location.distance_km_from_reported_coordinate) ? ` · ${Number(location.distance_km_from_reported_coordinate).toFixed(0)} km` : ''}</div></div><div class="comparison-cards">${cards}</div><div class="climate-footer-note"><span>${html(windowNote)} Historical precipitation is converted to climatological mean mm/day for comparison. IFS values are model-forecast context, not observations or causal attribution.</span><a href="methods.html#climate-context">Methods & definitions →</a></div>`;
  }

  async function renderInlineClimate() {
    const token = ++renderToken;
    if (typeof mode !== 'undefined' && mode === 'emerging') return;
    if (typeof expanded === 'undefined' || !expanded || typeof visible !== 'function') return;
    const event = visible().find(x => x.id === expanded);
    if (!event) return;
    const card = document.getElementById(`event-${event.id}`);
    const details = card?.querySelector('.event-details');
    if (!details) return;
    let slot = details.querySelector('.climate-inline-context');
    if (!slot) {
      slot = document.createElement('section');
      slot.className = 'climate-inline-context';
      slot.setAttribute('aria-live', 'polite');
      slot.onclick = ev => ev.stopPropagation();
      details.append(slot);
    } else if (slot !== details.lastElementChild) {
      details.append(slot);
    }

    const ref = event.climate_context || {};
    if (ref.status === 'unavailable') {
      slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3></div></div><div class="climate-status climate-error">${html(ref.reason || 'No suitable CRU land grid cell was available for this event location.')}</div>`;
      return;
    }

    // A repository snapshot can lag behind the already-published per-event climate file.
    // Never leave the UI stuck on a stale "waiting" flag: try the deterministic
    // per-event path directly whenever the snapshot reference is missing or not ready.
    const contextPath = ref.status === 'ready' && ref.path ? ref.path : fallbackPathForEvent(event);
    slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3><p>Loading same-month historical normals and recent 7-day context…</p></div></div><div class="climate-status"><span class="climate-loading">Checking climate context…</span></div>`;

    try {
      const ctx = await fetchContext(contextPath);
      if (token !== renderToken) return;
      const currentSlot = document.getElementById(`event-${event.id}`)?.querySelector('.climate-inline-context');
      if (currentSlot) currentSlot.innerHTML = renderReady(ctx, event);
    } catch (err) {
      if (token !== renderToken) return;
      const currentSlot = document.getElementById(`event-${event.id}`)?.querySelector('.climate-inline-context');
      if (!currentSlot) return;
      if (ref.status === 'ready' && ref.path) {
        currentSlot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3></div></div><div class="climate-status climate-error">Climate context could not be loaded · ${html(err?.message || err)}</div>`;
      } else {
        currentSlot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3></div></div><div class="climate-status">Climate context is not available for this event yet. The page will retry whenever the event is reopened or the data are refreshed.</div>`;
      }
    }
  }

  if (typeof renderCards === 'function') {
    const baseRenderCards = renderCards;
    renderCards = function () { baseRenderCards(); renderInlineClimate(); };
  }
  if (typeof renderDetail === 'function') {
    const baseRenderDetail = renderDetail;
    renderDetail = function () { baseRenderDetail(); removeSideClimateHint(); };
  }
  if (typeof renderDetail === 'function') renderDetail();
  if (typeof renderCards === 'function') renderCards();
})();
