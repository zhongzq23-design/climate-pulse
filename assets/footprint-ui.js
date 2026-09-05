'use strict';

// Compact mapped-event footprint shown FIRST in expanded details, only when a
// backend polygon exists. The map is a local equirectangular orientation view.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  function normLon(lon, center) { let x = Number(lon), c = Number(center); while (x - c > 180) x -= 360; while (x - c < -180) x += 360; return x; }
  function wrapLon(lon) { let x = Number(lon); while (x > 180) x -= 360; while (x <= -180) x += 360; return x; }
  function fmtLon(lon) { const x = wrapLon(lon); return `${Math.abs(x).toFixed(Math.abs(x) < 10 ? 1 : 0)}°${x >= 0 ? 'E' : 'W'}`; }
  function fmtLat(lat) { const y = Number(lat); return `${Math.abs(y).toFixed(Math.abs(y) < 10 ? 1 : 0)}°${y >= 0 ? 'N' : 'S'}`; }
  function collectPoints(geometry, centerLon) {
    const pts = [];
    const walk = node => {
      if (!Array.isArray(node)) return;
      if (node.length >= 2 && finite(node[0]) && finite(node[1])) { pts.push([normLon(node[0], centerLon), Number(node[1])]); return; }
      node.forEach(walk);
    };
    walk(geometry?.coordinates); return pts;
  }
  function niceStep(span) { return [.1, .25, .5, 1, 2, 5, 10, 20, 30, 45, 60, 90].find(s => span / s <= 6) || 90; }
  function ringPath(ring, centerLon, x, y) {
    if (!Array.isArray(ring) || !ring.length) return '';
    return ring.map((p, i) => {
      if (!Array.isArray(p) || !finite(p[0]) || !finite(p[1])) return '';
      const xx = x(normLon(p[0], centerLon)), yy = y(Number(p[1]));
      return `${i === 0 ? 'M' : 'L'}${xx.toFixed(2)},${yy.toFixed(2)}`;
    }).join('') + 'Z';
  }
  function geometryPath(geometry, centerLon, x, y) {
    if (!geometry) return '';
    if (geometry.type === 'Polygon') return (geometry.coordinates || []).map(r => ringPath(r, centerLon, x, y)).join('');
    if (geometry.type === 'MultiPolygon') return (geometry.coordinates || []).flatMap(poly => (poly || []).map(r => ringPath(r, centerLon, x, y))).join('');
    return '';
  }
  function renderMap(doc, e) {
    const geometry = doc.geometry;
    const centerLon = finite(doc.reported_center?.lon) ? Number(doc.reported_center.lon) : Number(e.lon);
    const centerLat = finite(doc.reported_center?.lat) ? Number(doc.reported_center.lat) : Number(e.lat);
    const pts = collectPoints(geometry, centerLon);
    if (!pts.length) return '';
    let minLon = Math.min(...pts.map(p => p[0])), maxLon = Math.max(...pts.map(p => p[0]));
    let minLat = Math.min(...pts.map(p => p[1])), maxLat = Math.max(...pts.map(p => p[1]));
    let lonSpan = maxLon - minLon, latSpan = maxLat - minLat;
    const minViewSpan = .35;
    if (lonSpan < minViewSpan) { const c = (minLon + maxLon) / 2; minLon = c - minViewSpan / 2; maxLon = c + minViewSpan / 2; lonSpan = minViewSpan; }
    if (latSpan < minViewSpan) { const c = (minLat + maxLat) / 2; minLat = c - minViewSpan / 2; maxLat = c + minViewSpan / 2; latSpan = minViewSpan; }
    const padLon = lonSpan * .10, padLat = latSpan * .12;
    minLon -= padLon; maxLon += padLon; minLat -= padLat; maxLat += padLat;
    minLat = Math.max(-90, minLat); maxLat = Math.min(90, maxLat);
    const W = 1040, H = 360, m = { l: 68, r: 26, t: 24, b: 52 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const x = lon => m.l + (lon - minLon) / (maxLon - minLon) * pw;
    const y = lat => m.t + (maxLat - lat) / (maxLat - minLat) * ph;
    const lonStep = niceStep(maxLon - minLon), latStep = niceStep(maxLat - minLat);
    const lonTicks = [], latTicks = [];
    for (let v = Math.ceil(minLon / lonStep) * lonStep; v <= maxLon + 1e-9; v += lonStep) lonTicks.push(v);
    for (let v = Math.ceil(minLat / latStep) * latStep; v <= maxLat + 1e-9; v += latStep) latTicks.push(v);
    const gridLon = lonTicks.map(v => `<line class="footprint-gridline" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${H - m.b}"></line><text class="footprint-axistext" x="${x(v)}" y="${H - 24}" text-anchor="middle">${fmtLon(v)}</text>`).join('');
    const gridLat = latTicks.map(v => `<line class="footprint-gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"></line><text class="footprint-axistext" x="${m.l - 10}" y="${y(v) + 4}" text-anchor="end">${fmtLat(v)}</text>`).join('');
    const path = geometryPath(geometry, centerLon, x, y); if (!path) return '';
    const pointLon = normLon(centerLon, centerLon);
    const centerVisible = pointLon >= minLon && pointLon <= maxLon && centerLat >= minLat && centerLat <= maxLat;
    const centerMark = centerVisible ? `<circle class="footprint-center-halo" cx="${x(pointLon)}" cy="${y(centerLat)}" r="8"></circle><circle class="footprint-center" cx="${x(pointLon)}" cy="${y(centerLat)}" r="4"></circle>` : '';
    const method = esc(doc.footprint_method || e.footprint?.method || 'mapped event polygon');
    return `<div class="footprint-inline-head"><div><h3>Mapped event footprint</h3><p>${method} · geographic outline for orientation</p></div><div class="footprint-meta">GDACS polygon<br>simplified display geometry</div></div><div class="footprint-map-card"><svg class="footprint-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Mapped event footprint with latitude and longitude grid"><rect class="footprint-bg" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" rx="10"></rect>${gridLon}${gridLat}<path class="footprint-shape" d="${path}" fill-rule="evenodd"></path>${centerMark}<text class="footprint-axislabel" x="${m.l + pw / 2}" y="${H - 5}" text-anchor="middle">Longitude</text><text class="footprint-axislabel" x="17" y="${m.t + ph / 2}" transform="rotate(-90 17 ${m.t + ph / 2})" text-anchor="middle">Latitude</text></svg></div><div class="footprint-note">The polygon is simplified for browser display. Population exposure uses the unsimplified mapped footprint. The point marks the reported event coordinate.</div>`;
  }
  async function fetchFootprint(path) {
    if (CACHE.has(path)) return CACHE.get(path);
    const candidates = [path, `https://raw.githubusercontent.com/zhongzq23-design/climate-pulse/main/${path}`];
    let last = null;
    for (const url of candidates) {
      try { const r = await fetch(url, { cache: 'no-store' }); if (!r.ok) throw new Error(`HTTP ${r.status}`); const j = await r.json(); CACHE.set(path, j); return j; }
      catch (err) { last = err; }
    }
    throw last || new Error('Footprint unavailable');
  }
  async function renderInlineFootprint() {
    const token = ++renderToken;
    if (mode === 'emerging' || !expanded) return;
    const e = visible().find(x => x.id === expanded); if (!e) return;
    const ref = e.footprint || {}; if (ref.status !== 'ready' || !ref.path) return;
    const card = document.getElementById(`event-${e.id}`), details = card?.querySelector('.event-details'); if (!details) return;
    let slot = details.querySelector('.event-footprint-context');
    if (!slot) { slot = document.createElement('section'); slot.className = 'event-footprint-context'; slot.setAttribute('aria-live', 'polite'); slot.onclick = ev => ev.stopPropagation(); details.prepend(slot); }
    else if (slot !== details.firstElementChild) details.prepend(slot);
    slot.innerHTML = '<div class="footprint-status"><span class="footprint-loading">Loading mapped footprint…</span></div>';
    try {
      const doc = await fetchFootprint(ref.path); if (token !== renderToken) return;
      const current = document.getElementById(`event-${e.id}`)?.querySelector('.event-footprint-context'); if (!current) return;
      const html = renderMap(doc, e); if (!html) { current.remove(); return; } current.innerHTML = html;
    } catch (_) {
      if (token !== renderToken) return;
      document.getElementById(`event-${e.id}`)?.querySelector('.event-footprint-context')?.remove();
    }
  }
  const baseRenderCards = renderCards;
  renderCards = function () { baseRenderCards(); renderInlineFootprint(); };
  renderCards();
})();