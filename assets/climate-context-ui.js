'use strict';

// Annual-only climate-context UI. The climate block is rendered inside the
// expanded event card so the map area stays visually clean.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  const C = { tmp: '#c94f3d', pre: '#2e7aa5', vpd: '#c8871b' };
  const META = {
    tmp: { label: 'Temperature', unit: '°C' },
    pre: { label: 'Precipitation', unit: 'mm/year' },
    vpd: { label: 'VPD', unit: 'hPa' }
  };

  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const signed = (v, digits = 1) => {
    if (!finite(v)) return '–';
    const n = Number(v);
    return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`;
  };
  const valueText = (v, variable) => {
    if (!finite(v)) return '–';
    const n = Number(v);
    if (variable === 'tmp') return `${n.toFixed(1)} °C`;
    if (variable === 'vpd') return `${n.toFixed(2)} hPa`;
    return `${Math.round(n).toLocaleString()} mm/year`;
  };
  const pText = p => {
    if (!finite(p)) return 'p unavailable';
    const n = Number(p);
    if (n < .001) return 'p < 0.001';
    return `p = ${n.toFixed(3)}`;
  };
  const trendText = (variable, s) => {
    if (!s || !finite(s.trend_1901_2025_per_century)) return '–';
    if (variable === 'tmp') return `${signed(s.trend_1901_2025_per_century, 2)} °C / century`;
    if (variable === 'vpd') return `${signed(s.trend_1901_2025_per_century, 2)} hPa / century`;
    if (finite(s.trend_percent_per_century)) return `${signed(s.trend_percent_per_century, 1)}% / century`;
    return `${signed(s.trend_1901_2025_per_century, 0)} mm / century`;
  };
  const changeText = (variable, s) => {
    const d = s?.change || {};
    if (!finite(d.absolute)) return '–';
    if (variable === 'tmp') return `${signed(d.absolute, 1)} °C`;
    if (variable === 'vpd') return `${signed(d.absolute, 2)} hPa`;
    return finite(d.percent) ? `${signed(d.percent, 1)}% (${signed(d.absolute, 0)} mm)` : `${signed(d.absolute, 0)} mm`;
  };

  function movingAverage(values, width = 5) {
    const half = Math.floor(width / 2);
    return values.map((_, i) => {
      const a = Math.max(0, i - half), b = Math.min(values.length, i + half + 1);
      const x = values.slice(a, b).filter(finite).map(Number);
      return x.length ? x.reduce((p, q) => p + q, 0) / x.length : null;
    });
  }
  function trendSeries(years, summary) {
    if (!summary || !finite(summary.theil_sen_slope_per_year) || !finite(summary.theil_sen_intercept)) return years.map(() => null);
    const slope = Number(summary.theil_sen_slope_per_year), intercept = Number(summary.theil_sen_intercept);
    return years.map(y => intercept + slope * Number(y));
  }
  function range(values, includeZero = false) {
    const x = values.filter(finite).map(Number);
    if (!x.length) return [0, 1];
    let lo = Math.min(...x), hi = Math.max(...x);
    if (includeZero) lo = Math.min(0, lo);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * .08;
    return [lo - (includeZero && lo === 0 ? 0 : pad), hi + pad];
  }
  function pathFor(values, xFn, yFn) {
    let d = '', open = false;
    values.forEach((v, i) => {
      if (!finite(v)) { open = false; return; }
      d += `${open ? 'L' : 'M'}${xFn(i).toFixed(2)},${yFn(Number(v)).toFixed(2)}`;
      open = true;
    });
    return d;
  }
  function ticks(lo, hi, n = 4) {
    return Array.from({ length: n + 1 }, (_, i) => lo + (hi - lo) * i / n);
  }
  function shortNum(v) {
    const a = Math.abs(v);
    if (a >= 1000) return `${(v / 1000).toFixed(a >= 10000 ? 0 : 1)}k`;
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    return v.toFixed(2);
  }

  function comboChart(years, temp, precip, tSummary, pSummary) {
    const W = 1040, H = 285, m = { l: 62, r: 76, t: 20, b: 38 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const t5 = movingAverage(temp), p5 = movingAverage(precip);
    const tt = trendSeries(years, tSummary), pt = trendSeries(years, pSummary);
    const [t0, t1] = range([...temp, ...t5, ...tt]);
    const [, p1raw] = range([...precip, ...p5, ...pt], true);
    const p0 = 0, p1 = Math.max(1, p1raw);
    const x = i => m.l + (years.length === 1 ? 0 : i / (years.length - 1) * pw);
    const yt = v => m.t + (t1 - v) / (t1 - t0) * ph;
    const yp = v => m.t + (p1 - v) / (p1 - p0) * ph;
    const bw = Math.max(1.1, pw / years.length * .68);
    const grid = ticks(t0, t1).map(v => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${yt(v)}" y2="${yt(v)}"></line><text class="axistext" x="${m.l - 8}" y="${yt(v) + 4}" text-anchor="end">${shortNum(v)}</text>`).join('');
    const right = ticks(p0, p1).map(v => `<text class="axistext" x="${W - m.r + 9}" y="${yp(v) + 4}" text-anchor="start">${shortNum(v)}</text>`).join('');
    const bars = precip.map((v, i) => finite(v) ? `<rect class="prebar" x="${(x(i) - bw / 2).toFixed(2)}" y="${yp(Number(v)).toFixed(2)}" width="${bw.toFixed(2)}" height="${Math.max(0, yp(0) - yp(Number(v))).toFixed(2)}" fill="${C.pre}"></rect>` : '').join('');
    const ix = [0, 29, 59, 89, years.length - 1].filter((v, i, a) => v >= 0 && v < years.length && a.indexOf(v) === i);
    const xt = ix.map(i => `<text class="axistext" x="${x(i)}" y="${H - 11}" text-anchor="middle">${years[i]}</text>`).join('');
    return `<svg class="climate-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Annual temperature and precipitation history from 1901 to 2025">
      ${grid}${right}${bars}
      <path class="rawline" d="${pathFor(temp, x, yt)}" stroke="${C.tmp}"></path>
      <path class="smoothline" d="${pathFor(t5, x, yt)}" stroke="${C.tmp}"></path>
      <path class="trendline" d="${pathFor(tt, x, yt)}" stroke="${C.tmp}"></path>
      <path class="presmooth" d="${pathFor(p5, x, yp)}" stroke="${C.pre}"></path>
      <path class="trendline" d="${pathFor(pt, x, yp)}" stroke="${C.pre}"></path>
      ${xt}
      <text class="axislabel" x="16" y="${m.t + ph / 2}" transform="rotate(-90 16 ${m.t + ph / 2})" text-anchor="middle">Temperature · °C</text>
      <text class="axislabel" x="${W - 13}" y="${m.t + ph / 2}" transform="rotate(90 ${W - 13} ${m.t + ph / 2})" text-anchor="middle">Precipitation · mm/year</text>
    </svg>`;
  }

  function lineChart(years, values, variable, summary) {
    const W = 1040, H = 230, m = { l: 62, r: 28, t: 18, b: 36 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const sm = movingAverage(values), tr = trendSeries(years, summary);
    const [y0, y1] = range([...values, ...sm, ...tr]);
    const x = i => m.l + (years.length === 1 ? 0 : i / (years.length - 1) * pw);
    const y = v => m.t + (y1 - v) / (y1 - y0) * ph;
    const grid = ticks(y0, y1).map(v => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"></line><text class="axistext" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${shortNum(v)}</text>`).join('');
    const ix = [0, 29, 59, 89, years.length - 1].filter((v, i, a) => v >= 0 && v < years.length && a.indexOf(v) === i);
    const xt = ix.map(i => `<text class="axistext" x="${x(i)}" y="${H - 10}" text-anchor="middle">${years[i]}</text>`).join('');
    return `<svg class="climate-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Annual ${META[variable].label} history from 1901 to 2025">
      ${grid}<path class="rawline" d="${pathFor(values, x, y)}" stroke="${C[variable]}"></path><path class="smoothline" d="${pathFor(sm, x, y)}" stroke="${C[variable]}"></path><path class="trendline" d="${pathFor(tr, x, y)}" stroke="${C[variable]}"></path>${xt}
      <text class="axislabel" x="16" y="${m.t + ph / 2}" transform="rotate(-90 16 ${m.t + ph / 2})" text-anchor="middle">${META[variable].label} · ${META[variable].unit}</text>
    </svg>`;
  }

  function trendCard(variable, ctx) {
    const s = ctx.annual?.summary?.[variable];
    if (!s) return '';
    const significant = s.trend_significant_p05 === true;
    return `<div class="trend-card"><h4 style="color:${C[variable]}">${esc(META[variable].label)}</h4><div class="trend-main"><div class="trend-value" style="color:${C[variable]}">${esc(trendText(variable, s))}</div><span class="sig-badge ${significant ? 'sig-yes' : 'sig-no'}">${significant ? 'significant' : 'not significant'}</span></div><div class="trend-sub">Mann–Kendall ${esc(pText(s.mann_kendall_p))}<br>Recent 2016–2025 mean: <strong>${esc(valueText(s.recent_2016_2025, variable))}</strong><br>Change vs 1901–1930: <strong>${esc(changeText(variable, s))}</strong></div></div>`;
  }

  async function fetchContext(path) {
    if (CACHE.has(path)) return CACHE.get(path);
    const candidates = [path, `https://raw.githubusercontent.com/zhongzq23-design/climate-pulse/main/${path}`];
    let last = null;
    for (const url of candidates) {
      try {
        const r = await fetch(url, { cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        CACHE.set(path, j);
        return j;
      } catch (err) { last = err; }
    }
    throw last || new Error('Climate context unavailable');
  }

  function replaceDetailHint(e) {
    const panel = $('detailPanel');
    if (!panel || !e) return;
    const label = [...panel.querySelectorAll('.section-label')].find(x => /local warming context|climate background/i.test(x.textContent || ''));
    const box = label?.nextElementSibling;
    if (label) label.textContent = 'Climate background';
    if (box?.classList.contains('climate-box')) {
      box.innerHTML = e.climate_context?.status === 'ready'
        ? `Open <strong>Show full details</strong> in the event feed to view the 1901–2025 CRU climate background, Theil–Sen trend and Mann–Kendall significance.`
        : `Long-term CRU climate background is queued for backend enrichment. <strong>Climate context is not event attribution.</strong>`;
    }
  }

  function renderReady(ctx, e) {
    const vars = Array.isArray(ctx.variable_profile) ? ctx.variable_profile : (e.climate_context?.variables || ['tmp', 'pre']);
    const years = ctx.annual?.years || [], series = ctx.annual?.series || {}, summary = ctx.annual?.summary || {};
    const dist = ctx.cru_grid?.distance_km_from_reported_coordinate;
    const method = ctx.cru_grid?.selection_method === 'nearest_valid_land_cell' ? 'nearest valid CRU land cell' : 'nearest CRU grid cell';
    const cards = vars.map(v => trendCard(v, ctx)).join('');
    const combo = vars.includes('tmp') && vars.includes('pre') && series.tmp && series.pre
      ? `<div class="climate-chart-card"><div class="climate-chart-title"><h4>Temperature & precipitation · annual history</h4><div class="chart-legend"><span class="legend-line"><span class="legend-swatch" style="background:${C.tmp}"></span>5-year temperature</span><span class="legend-line" style="color:${C.tmp}"><span class="legend-dash"></span>Theil–Sen</span><span class="legend-line"><span class="legend-bar" style="background:${C.pre}"></span>annual precipitation</span><span class="legend-line"><span class="legend-swatch" style="background:${C.pre};height:2px"></span>5-year precipitation</span><span class="legend-line" style="color:${C.pre}"><span class="legend-dash"></span>Theil–Sen</span></div></div>${comboChart(years, series.tmp, series.pre, summary.tmp, summary.pre)}</div>` : '';
    const vpd = vars.includes('vpd') && series.vpd
      ? `<div class="climate-chart-card"><div class="climate-chart-title"><h4>Atmospheric dryness · VPD</h4><div class="chart-legend"><span class="legend-line"><span class="legend-swatch" style="background:${C.vpd}"></span>5-year VPD</span><span class="legend-line" style="color:${C.vpd}"><span class="legend-dash"></span>Theil–Sen</span></div></div>${lineChart(years, series.vpd, 'vpd', summary.vpd)}</div>` : '';
    return `<div class="climate-inline-head"><div><h3>Climate background · 1901–2025</h3><p>Annual CRU-TS v4.10 background for this event location. Variables are selected by hazard type.</p></div><div class="climate-context-meta">CRU-TS v4.10 · 0.5°<br>${esc(method)}${finite(dist) ? ` · ${Number(dist).toFixed(0)} km from reported coordinate` : ''}</div></div><div class="trend-note"><strong>Trend method:</strong> Theil–Sen median slope for 1901–2025. Significance is reported with a two-sided Mann–Kendall test at p &lt; 0.05. This is descriptive climate context, not causal event attribution.</div><div class="trend-cards">${cards}</div>${combo}${vpd}<div class="climate-footer-note"><span>Annual raw values are shown faintly; 5-year moving means are included for readability. Mann–Kendall p-values are not adjusted for serial autocorrelation.</span><a href="methods.html#climate-context">Methods & definitions →</a></div>`;
  }

  async function renderInlineClimate() {
    const token = ++renderToken;
    if (mode === 'emerging' || !expanded) return;
    const e = visible().find(x => x.id === expanded);
    if (!e) return;
    const card = document.getElementById(`event-${e.id}`), details = card?.querySelector('.event-details');
    if (!details) return;
    let slot = details.querySelector('.climate-inline-context');
    if (!slot) {
      slot = document.createElement('section');
      slot.className = 'climate-inline-context';
      slot.setAttribute('aria-live', 'polite');
      slot.onclick = ev => ev.stopPropagation();
      details.prepend(slot);
      const tech = document.createElement('div');
      tech.className = 'event-tech-label';
      tech.textContent = 'Event & exposure details';
      slot.insertAdjacentElement('afterend', tech);
    }
    const ref = e.climate_context || {};
    if (ref.status === 'unavailable') {
      slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate background · 1901–2025</h3></div></div><div class="climate-status climate-error">${esc(ref.reason || 'No suitable CRU land grid cell was available for this event location.')}</div>`;
      return;
    }
    if (ref.status !== 'ready' || !ref.path) {
      slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate background · 1901–2025</h3><p>CRU background is queued for backend enrichment.</p></div></div><div class="climate-status"><span class="climate-loading">Waiting for the next monitoring run…</span></div>`;
      return;
    }
    slot.innerHTML = `<div class="climate-inline-head"><div><h3>Climate background · 1901–2025</h3><p>Loading annual CRU climate background…</p></div></div><div class="climate-status"><span class="climate-loading">Loading climate context…</span></div>`;
    try {
      const ctx = await fetchContext(ref.path);
      if (token !== renderToken) return;
      const current = document.getElementById(`event-${e.id}`)?.querySelector('.climate-inline-context');
      if (current) current.innerHTML = renderReady(ctx, e);
    } catch (err) {
      if (token !== renderToken) return;
      const current = document.getElementById(`event-${e.id}`)?.querySelector('.climate-inline-context');
      if (current) current.innerHTML = `<div class="climate-inline-head"><div><h3>Climate background · 1901–2025</h3></div></div><div class="climate-status climate-error">Climate context could not be loaded yet · ${esc(err?.message || err)}</div>`;
    }
  }

  const baseRenderCards = renderCards;
  renderCards = function () {
    baseRenderCards();
    renderInlineClimate();
  };

  const baseRenderDetail = renderDetail;
  renderDetail = function () {
    baseRenderDetail();
    replaceDetailHint(chosen());
  };

  renderDetail();
  renderCards();
})();
