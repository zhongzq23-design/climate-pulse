'use strict';

// Annual-only climate-context UI. Climate Pulse intentionally keeps the public
// event view focused on long-term CRU-TS v4.10 change (1901-2025). Monthly or
// near-real-time anomaly work is kept outside this public panel for now.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  const C = { tmp: '#c94f3d', pre: '#2e7aa5', vpd: '#c8871b' };
  const META = {
    tmp: { label: 'Temperature', unit: '°C', note: 'Annual mean' },
    pre: { label: 'Precipitation', unit: 'mm/year', note: 'Annual total' },
    vpd: { label: 'VPD', unit: 'hPa', note: 'Annual mean' }
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
  const changeText = (variable, change) => {
    if (!change || !finite(change.absolute)) return '–';
    if (variable === 'tmp') return `${signed(change.absolute, 1)} °C`;
    if (variable === 'vpd') return `${signed(change.absolute, 2)} hPa`;
    if (finite(change.percent)) return `${signed(change.percent, 1)}%`;
    return `${signed(change.absolute, 0)} mm`;
  };
  const trendText = (variable, s) => {
    if (!s) return '–';
    if (variable === 'pre' && finite(s.trend_percent_per_century)) return `${signed(s.trend_percent_per_century, 1)}% / century`;
    if (!finite(s.trend_1901_2025_per_century)) return '–';
    if (variable === 'tmp') return `${signed(s.trend_1901_2025_per_century, 2)} °C / century`;
    if (variable === 'vpd') return `${signed(s.trend_1901_2025_per_century, 2)} hPa / century`;
    return `${signed(s.trend_1901_2025_per_century, 0)} mm / century`;
  };

  function movingAverage(values, width = 5) {
    const half = Math.floor(width / 2);
    return values.map((_, i) => {
      const a = Math.max(0, i - half), b = Math.min(values.length, i + half + 1);
      const x = values.slice(a, b).filter(finite).map(Number);
      return x.length ? x.reduce((p, q) => p + q, 0) / x.length : null;
    });
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

  function comboChart(years, temp, precip) {
    const W = 980, H = 286, m = { l: 62, r: 76, t: 24, b: 40 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const t5 = movingAverage(temp), p5 = movingAverage(precip);
    const [t0, t1] = range([...temp, ...t5]);
    const [, p1raw] = range([...precip, ...p5], true);
    const p0 = 0, p1 = Math.max(1, p1raw);
    const x = i => m.l + (years.length === 1 ? 0 : i / (years.length - 1) * pw);
    const yt = v => m.t + (t1 - v) / (t1 - t0) * ph;
    const yp = v => m.t + (p1 - v) / (p1 - p0) * ph;
    const bw = Math.max(1.15, pw / years.length * .72);
    const grid = ticks(t0, t1).map(v => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${yt(v)}" y2="${yt(v)}"></line><text class="axistext" x="${m.l - 8}" y="${yt(v) + 4}" text-anchor="end">${shortNum(v)}</text>`).join('');
    const right = ticks(p0, p1).map(v => `<text class="axistext" x="${W - m.r + 9}" y="${yp(v) + 4}" text-anchor="start">${shortNum(v)}</text>`).join('');
    const bars = precip.map((v, i) => finite(v) ? `<rect class="prebar" x="${(x(i) - bw / 2).toFixed(2)}" y="${yp(Number(v)).toFixed(2)}" width="${bw.toFixed(2)}" height="${Math.max(0, yp(0) - yp(Number(v))).toFixed(2)}" fill="${C.pre}"></rect>` : '').join('');
    const ix = [0, 24, 49, 74, 99, years.length - 1].filter((v, i, a) => v >= 0 && v < years.length && a.indexOf(v) === i);
    const xt = ix.map(i => `<text class="axistext" x="${x(i)}" y="${H - 11}" text-anchor="middle">${years[i]}</text>`).join('');
    return `<svg class="climate-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="CRU annual temperature and precipitation, 1901 to 2025">
      ${grid}${right}${bars}
      <path class="rawline" d="${pathFor(temp, x, yt)}" stroke="${C.tmp}"></path>
      <path class="smoothline" d="${pathFor(t5, x, yt)}" stroke="${C.tmp}"></path>
      <path class="presmooth" d="${pathFor(p5, x, yp)}" stroke="${C.pre}"></path>
      ${xt}
      <text class="axislabel" x="16" y="${m.t + ph / 2}" transform="rotate(-90 16 ${m.t + ph / 2})" text-anchor="middle">Temperature · °C</text>
      <text class="axislabel" x="${W - 13}" y="${m.t + ph / 2}" transform="rotate(90 ${W - 13} ${m.t + ph / 2})" text-anchor="middle">Precipitation · mm/year</text>
    </svg>`;
  }

  function lineChart(years, values, variable) {
    const W = 980, H = 224, m = { l: 62, r: 28, t: 20, b: 36 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const sm = movingAverage(values);
    const [y0, y1] = range([...values, ...sm]);
    const x = i => m.l + (years.length === 1 ? 0 : i / (years.length - 1) * pw);
    const y = v => m.t + (y1 - v) / (y1 - y0) * ph;
    const grid = ticks(y0, y1).map(v => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"></line><text class="axistext" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${shortNum(v)}</text>`).join('');
    const ix = [0, 24, 49, 74, 99, years.length - 1].filter((v, i, a) => v >= 0 && v < years.length && a.indexOf(v) === i);
    const xt = ix.map(i => `<text class="axistext" x="${x(i)}" y="${H - 10}" text-anchor="middle">${years[i]}</text>`).join('');
    return `<svg class="climate-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="CRU annual ${META[variable].label}, 1901 to 2025">
      ${grid}<path class="rawline" d="${pathFor(values, x, y)}" stroke="${C[variable]}"></path><path class="smoothline" d="${pathFor(sm, x, y)}" stroke="${C[variable]}"></path>${xt}
      <text class="axislabel" x="16" y="${m.t + ph / 2}" transform="rotate(-90 16 ${m.t + ph / 2})" text-anchor="middle">${META[variable].label} · ${META[variable].unit}</text>
    </svg>`;
  }

  function annualMetric(variable, ctx) {
    const s = ctx.annual?.summary?.[variable];
    if (!s) return '';
    return `<div class="annual-metric" style="--metric-color:${C[variable]}">
      <div class="metric-kicker">${esc(META[variable].note)}</div>
      <h4>${esc(META[variable].label)}</h4>
      <div class="metric-primary">${esc(trendText(variable, s))}</div>
      <div class="metric-primary-label">Long-term trend · 1901–2025</div>
      <div class="metric-row"><span>Recent 10-year mean</span><strong>${esc(valueText(s.recent_2016_2025, variable))}</strong></div>
      <div class="metric-row"><span>Change vs 1901–1930</span><strong>${esc(changeText(variable, s.change))}</strong></div>
    </div>`;
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
    const label = [...panel.querySelectorAll('.section-label')].find(x => /local warming context/i.test(x.textContent || ''));
    const box = label?.nextElementSibling;
    if (box?.classList.contains('climate-box')) {
      box.innerHTML = e.climate_context?.status === 'ready'
        ? `Long-term <strong>CRU-TS v4.10</strong> climate background (1901–2025) is shown below. Climate context is not event attribution.`
        : `Long-term CRU climate background will appear below after backend enrichment. <strong>Climate context is not event attribution.</strong>`;
    }
  }

  function renderReady(ctx, e) {
    const vars = Array.isArray(ctx.variable_profile) ? ctx.variable_profile : (e.climate_context?.variables || ['tmp', 'pre']);
    const years = ctx.annual?.years || [];
    const series = ctx.annual?.series || {};
    const dist = ctx.cru_grid?.distance_km_from_reported_coordinate;
    const method = ctx.cru_grid?.selection_method === 'nearest_valid_land_cell' ? 'nearest valid CRU land cell' : 'nearest CRU grid cell';
    const metrics = vars.map(v => annualMetric(v, ctx)).join('');
    const profile = vars.map(v => `<span class="context-variable" style="--chip:${C[v]}">${esc(META[v].label)}</span>`).join('');
    const combo = vars.includes('tmp') && vars.includes('pre') && series.tmp && series.pre
      ? `<div class="climate-chart-card"><div class="climate-chart-title"><div><div class="chart-kicker">Long-term annual record</div><h3>Temperature + precipitation</h3></div><div class="chart-legend"><span class="legend-line"><span class="legend-swatch" style="background:${C.tmp}"></span>Temperature · 5-year mean</span><span class="legend-line"><span class="legend-bar" style="background:${C.pre}"></span>Precipitation · annual</span><span class="legend-line"><span class="legend-swatch" style="background:${C.pre};height:2px"></span>Precipitation · 5-year mean</span></div></div>${comboChart(years, series.tmp, series.pre)}</div>` : '';
    const vpd = vars.includes('vpd') && series.vpd
      ? `<div class="climate-chart-card"><div class="climate-chart-title"><div><div class="chart-kicker">Atmospheric dryness</div><h3>VPD · 1901–2025</h3></div><div class="chart-legend"><span class="legend-line"><span class="legend-swatch" style="background:${C.vpd}"></span>VPD · 5-year mean</span></div></div>${lineChart(years, series.vpd, 'vpd')}</div>` : '';

    return `<div class="climate-context-head"><div><div class="context-eyebrow">Climate background · annual only</div><h2>${esc(e.title)}</h2><p>Long-term local climate change from 1901 to 2025. Variables are selected by hazard type; monthly anomalies are not used in this view.</p><div class="context-variable-row">${profile}</div></div><div class="climate-context-meta">CRU-TS v4.10 · 0.5°<br>${esc(method)}${finite(dist) ? ` · ${Number(dist).toFixed(0)} km from reported coordinate` : ''}</div></div>
      <div class="climate-context-body">
        <div class="annual-summary-intro"><div><strong>Long-term signal</strong><span>Trend is calculated from annual values over 1901–2025. Recent mean uses 2016–2025; early comparison uses 1901–1930.</span></div><span class="annual-only-pill">1901–2025</span></div>
        <div class="annual-metrics">${metrics}</div>
        ${combo}${vpd}
        <div class="climate-footer-note"><span>Thin marks show annual variability; stronger lines show a 5-year moving mean. This is climate background, not a causal attribution of the event.</span><a href="methods.html#climate-context">Methods →</a></div>
      </div>`;
  }

  async function renderClimateContext(e) {
    const panel = $('climateContextPanel');
    if (!panel) return;
    const token = ++renderToken;
    if (mode === 'emerging') {
      panel.innerHTML = '<div class="climate-context-body"><div class="climate-status">Climate context will be added to an Emerging Signal only after editorial review.</div></div>';
      return;
    }
    if (!e) {
      panel.innerHTML = '<div class="climate-context-body"><div class="climate-status">Select an event to view its long-term climate background.</div></div>';
      return;
    }
    const ref = e.climate_context || {};
    if (ref.status === 'unavailable') {
      panel.innerHTML = `<div class="climate-context-head"><div><h2>Climate context · ${esc(e.title)}</h2></div></div><div class="climate-context-body"><div class="climate-status climate-error">${esc(ref.reason || 'No suitable CRU land grid cell was available for this event location.')}</div></div>`;
      return;
    }
    if (ref.status !== 'ready' || !ref.path) {
      panel.innerHTML = `<div class="climate-context-head"><div><h2>Climate context · ${esc(e.title)}</h2><p>1901–2025 CRU background is queued for backend enrichment.</p></div></div><div class="climate-context-body"><div class="climate-status"><span class="climate-loading">Waiting for the next monitoring run…</span></div></div>`;
      return;
    }
    panel.innerHTML = `<div class="climate-context-head"><div><h2>Climate context · ${esc(e.title)}</h2><p>Loading CRU annual record 1901–2025…</p></div></div><div class="climate-context-body"><div class="climate-status"><span class="climate-loading">Loading climate context…</span></div></div>`;
    try {
      const ctx = await fetchContext(ref.path);
      if (token !== renderToken) return;
      panel.innerHTML = renderReady(ctx, e);
    } catch (err) {
      if (token !== renderToken) return;
      panel.innerHTML = `<div class="climate-context-head"><div><h2>Climate context · ${esc(e.title)}</h2></div></div><div class="climate-context-body"><div class="climate-status climate-error">Climate context could not be loaded yet · ${esc(err?.message || err)}</div></div>`;
    }
  }

  const baseRenderDetail = renderDetail;
  renderDetail = function () {
    baseRenderDetail();
    const e = chosen();
    replaceDetailHint(e);
    renderClimateContext(e);
  };
})();
