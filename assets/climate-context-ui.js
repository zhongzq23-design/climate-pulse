'use strict';

// Same-month historical normals vs a latest complete 24-hour IFS forecast window.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  const COLORS = { tmp: '#c94f3d', pre: '#2e7aa5', vpd: '#c8871b' };
  const META = {
    tmp: { label: 'Temperature', unit: '°C' },
    pre: { label: 'Precipitation', unit: 'mm/day' },
    vpd: { label: 'VPD', unit: 'hPa' }
  };

  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const html = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
  const signed = (v, digits = 1) => finite(v) ? `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}` : '–';
  const fmt = (variable, value) => {
    if (!finite(value)) return '–';
    const n = Number(value);
    if (variable === 'tmp') return `${n.toFixed(1)} °C`;
    if (variable === 'vpd') return `${n.toFixed(2)} hPa`;
    return `${n.toFixed(n < 10 ? 1 : 0)} mm/day`;
  };
  const getPeriod = (ctx, key) => (ctx.comparison?.periods || []).find(p => p.key === key) || null;
  const compactUtc = iso => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })} ${String(d.getUTCHours()).padStart(2, '0')}Z`;
  };

  function deltaText(variable, current, modern) {
    if (!finite(current) || !finite(modern)) return 'Current comparison unavailable';
    const c = Number(current), m = Number(modern);
    if (variable === 'pre') {
      if (Math.abs(m) < 1e-9) return `${signed(c - m, 1)} mm/day vs 1981–2010`;
      const pct = (c - m) / m * 100;
      return `${signed(pct, 0)}% vs 1981–2010`;
    }
    return `${signed(c - m, variable === 'tmp' ? 1 : 2)} ${META[variable].unit} vs 1981–2010`;
  }

  function periodCell(variable, period, monthLabel) {
    if (!period) return '<div class="comparison-period is-missing"><div class="comparison-period-label">Unavailable</div><div class="comparison-value">–</div></div>';
    const value = period.values?.[variable];
    const historical = period.kind === 'same_calendar_month_climatology';
    const subtitle = historical
      ? `${monthLabel} mean · ${period.n_years || 30} years`
      : period.status === 'ready'
        ? `${compactUtc(period.window_start)} → ${compactUtc(period.window_end)}`
        : (period.reason || 'Earth Engine NRT unavailable');
    return `<div class="comparison-period${period.status === 'unavailable' ? ' is-missing' : ''}"><div class="comparison-period-label">${html(period.label)}</div><div class="comparison-value">${html(fmt(variable, value))}</div><div class="comparison-period-sub">${html(subtitle)}</div></div>`;
  }

  function variableCard(variable, ctx) {
    const monthLabel = ctx.comparison?.month_label || '';
    const early = getPeriod(ctx, '1901_1930');
    const modern = getPeriod(ctx, '1981_2010');
    const current = getPeriod(ctx, 'current_24h');
    const delta = deltaText(variable, current?.values?.[variable], modern?.values?.[variable]);
    return `<article class="comparison-card"><div class="comparison-variable"><span class="comparison-dot" style="background:${COLORS[variable]}"></span><div><h4>${html(META[variable].label)}</h4><div class="comparison-delta">${html(delta)}</div></div></div><div class="comparison-periods">${periodCell(variable, early, monthLabel)}${periodCell(variable, modern, monthLabel)}${periodCell(variable, current, monthLabel)}</div></article>`;
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
    return `<div class="climate-status">This event still has the previous annual climate-context schema. It will switch to the new same-month comparison on the next backend refresh.</div>`;
  }

  function renderReady(ctx, event) {
    if (!ctx.comparison?.periods) return legacyMessage();
    const vars = Array.isArray(ctx.variable_profile) ? ctx.variable_profile : (event.climate_context?.variables || ['tmp', 'pre']);
    const monthLabel = ctx.comparison.month_label || '';
    const current = getPeriod(ctx, 'current_24h');
    const location = ctx.comparison_location || {};
    const method = location.selection_method === 'nearest_valid_land_cell' ? 'nearest valid CRU land cell' : 'nearest CRU grid cell';
    const currentMeta = current?.status === 'ready'
      ? `IFS 0.25° · complete 24h ending ${html(compactUtc(current.window_end))}`
      : 'IFS current window pending';
    const cards = vars.filter(v => META[v]).map(v => variableCard(v, ctx)).join('');
    const windowNote = current?.status === 'ready'
      ? `Current 24h uses the latest fully elapsed IFS 0–24 h forecast window (${compactUtc(current.window_start)} → ${compactUtc(current.window_end)}), rather than an instantaneous value.`
      : `Historical normals are ready; Current 24h is unavailable until the backend Earth Engine credential is active.`;
    return `<div class="climate-inline-head"><div><h3>Climate comparison · ${html(monthLabel)}</h3><p>Same-calendar-month climate normals compared with a complete 24-hour current forecast window.</p></div><div class="climate-context-meta">CRU-TS v4.10 · 0.5°<br>${html(currentMeta)}<br>${html(method)}${finite(location.distance_km_from_reported_coordinate) ? ` · ${Number(location.distance_km_from_reported_coordinate).toFixed(0)} km` : ''}</div></div><div class="comparison-period-header"><span></span><span>Early climate</span><span>Modern normal</span><span>Current</span></div><div class="comparison-cards">${cards}</div><div class="climate-footer-note"><span>${html(windowNote)} Historical precipitation is converted to mean mm/day for a like-for-like comparison. Current IFS is a model forecast, not an observation or causal attribution.</span><a href="methods.html#climate-context">Methods & definitions →</a></div>`;
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
    if (ref.status !== 'ready' || !ref.path) {
      slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3><p>Waiting for climate enrichment.</p></div></div><div class="climate-status"><span class="climate-loading">Waiting for the next monitoring run…</span></div>`;
      return;
    }

    slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3><p>Loading same-month historical normals and current 24-hour context…</p></div></div><div class="climate-status"><span class="climate-loading">Loading climate context…</span></div>`;
    try {
      const ctx = await fetchContext(ref.path);
      if (token !== renderToken) return;
      const currentSlot = document.getElementById(`event-${event.id}`)?.querySelector('.climate-inline-context');
      if (currentSlot) currentSlot.innerHTML = renderReady(ctx, event);
    } catch (err) {
      if (token !== renderToken) return;
      const currentSlot = document.getElementById(`event-${event.id}`)?.querySelector('.climate-inline-context');
      if (currentSlot) currentSlot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate comparison</h3></div></div><div class="climate-status climate-error">Climate context could not be loaded yet · ${html(err?.message || err)}</div>`;
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
