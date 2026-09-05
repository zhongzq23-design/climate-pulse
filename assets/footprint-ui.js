'use strict';

// Compact mapped-event footprint shown FIRST in expanded details, only when a
// backend polygon exists. The map is a local equirectangular orientation view.
// Natural Earth 1:110m coastlines, country borders and selected populated places
// are loaded lazily as orientation context; failure of those optional layers does
// not suppress the authoritative event footprint.
(() => {
  const CACHE = new Map();
  let renderToken = 0;
  let baseContextPromise = null;
  const NATURAL_EARTH = {
    coast: 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_coastline.geojson',
    borders: 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_boundary_lines_land.geojson',
    cities: 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_populated_places_simple.geojson'
  };
  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  function normLon(lon, center) { let x = Number(lon), c = Number(center); while (x - c > 180) x -= 360; while (x - c < -180) x += 360; return x; }
  function wrapLon(lon) { let x = Number(lon); while (x > 180) x -= 360; while (x <= -180) x += 360; return x; }
  function fmtLon(lon) { const x = wrapLon(lon); return `${Math.abs(x).toFixed(Math.abs(x) < 10 ? 1 : 0)}° ${x >= 0 ? 'E' : 'W'}`; }
  function fmtLat(lat) { const y = Number(lat); return `${Math.abs(y).toFixed(Math.abs(y) < 10 ? 1 : 0)}° ${y >= 0 ? 'N' : 'S'}`; }
  function collectPoints(geometry, centerLon) {
    const pts = [];
    const walk = node => {
      if (!Array.isArray(node)) return;
      if (node.length >= 2 && finite(node[0]) && finite(node[1])) { pts.push([normLon(node[0], centerLon), Number(node[1])]); return; }
      node.forEach(walk);
    };
    walk(geometry?.coordinates); return pts;
  }
  function niceStep(span) { return [.25, .5, 1, 2, 5, 10, 15, 20, 30, 45, 60, 90].find(s => span / s <= 5) || 90; }
  function ringPath(ring, centerLon, x, y) {
    if (!Array.isArray(ring) || !ring.length) return '';
    let out = '', prevLon = null, started = false;
    for (const p of ring) {
      if (!Array.isArray(p) || !finite(p[0]) || !finite(p[1])) continue;
      const lon = normLon(p[0], centerLon), lat = Number(p[1]);
      if (prevLon !== null && Math.abs(lon - prevLon) > 180) started = false;
      out += `${started ? 'L' : 'M'}${x(lon).toFixed(2)},${y(lat).toFixed(2)}`;
      started = true; prevLon = lon;
    }
    return out ? out + 'Z' : '';
  }
  function geometryPath(geometry, centerLon, x, y) {
    if (!geometry) return '';
    if (geometry.type === 'Polygon') return (geometry.coordinates || []).map(r => ringPath(r, centerLon, x, y)).join('');
    if (geometry.type === 'MultiPolygon') return (geometry.coordinates || []).flatMap(poly => (poly || []).map(r => ringPath(r, centerLon, x, y))).join('');
    return '';
  }
  function lineCoordsPath(coords, centerLon, x, y) {
    if (!Array.isArray(coords) || !coords.length) return '';
    let out = '', prevLon = null, started = false;
    for (const p of coords) {
      if (!Array.isArray(p) || !finite(p[0]) || !finite(p[1])) continue;
      const lon = normLon(p[0], centerLon), lat = Number(p[1]);
      if (prevLon !== null && Math.abs(lon - prevLon) > 180) started = false;
      out += `${started ? 'L' : 'M'}${x(lon).toFixed(2)},${y(lat).toFixed(2)}`;
      started = true; prevLon = lon;
    }
    return out;
  }
  function lineGeometryPath(geometry, centerLon, x, y) {
    if (!geometry) return '';
    if (geometry.type === 'LineString') return lineCoordsPath(geometry.coordinates, centerLon, x, y);
    if (geometry.type === 'MultiLineString') return (geometry.coordinates || []).map(line => lineCoordsPath(line, centerLon, x, y)).join('');
    if (geometry.type === 'Polygon' || geometry.type === 'MultiPolygon') return geometryPath(geometry, centerLon, x, y);
    return '';
  }
  function featureTouchesView(feature, centerLon, minLon, maxLon, minLat, maxLat) {
    const pts = collectPoints(feature?.geometry, centerLon);
    return pts.some(([lon, lat]) => lon >= minLon - 1 && lon <= maxLon + 1 && lat >= minLat - 1 && lat <= maxLat + 1);
  }
  function renderLineLayer(fc, className, centerLon, x, y, minLon, maxLon, minLat, maxLat) {
    if (!fc?.features) return '';
    return fc.features
      .filter(f => featureTouchesView(f, centerLon, minLon, maxLon, minLat, maxLat))
      .map(f => lineGeometryPath(f.geometry, centerLon, x, y))
      .filter(Boolean)
      .map(d => `<path class="${className}" d="${d}"></path>`)
      .join('');
  }
  function cityPriority(feature) {
    const p = feature?.properties || {};
    const rank = finite(p.rank_max) ? Number(p.rank_max) : 0;
    const pop = finite(p.pop_max) ? Math.max(0, Number(p.pop_max)) : 0;
    return (p.adm0cap ? 120 : 0) + (p.worldcity ? 70 : 0) + (p.megacity ? 45 : 0) + rank * 7 + Math.log10(pop + 1) * 4;
  }
  function renderCities(fc, centerLon, x, y, minLon, maxLon, minLat, maxLat, lonSpan, latSpan) {
    if (!fc?.features) return '';
    const candidates = fc.features.map(f => {
      const c = f?.geometry?.coordinates;
      if (!Array.isArray(c) || !finite(c[0]) || !finite(c[1])) return null;
      const lon = normLon(c[0], centerLon), lat = Number(c[1]);
      if (lon < minLon || lon > maxLon || lat < minLat || lat > maxLat) return null;
      const p = f.properties || {};
      return { lon, lat, name: p.nameascii || p.name || '', score: cityPriority(f), pop: Number(p.pop_max || 0), capital: !!p.adm0cap };
    }).filter(Boolean).sort((a, b) => b.score - a.score);
    const maxCities = lonSpan > 35 || latSpan > 20 ? 5 : 7;
    const minPop = lonSpan > 50 ? 1000000 : lonSpan > 20 ? 300000 : 0;
    const chosen = [];
    for (const c of candidates) {
      if (!c.name) continue;
      if (!c.capital && c.pop < minPop && candidates.length > maxCities) continue;
      const px = x(c.lon), py = y(c.lat);
      if (chosen.some(q => Math.abs(px - q.px) < 82 && Math.abs(py - q.py) < 25)) continue;
      chosen.push({ ...c, px, py });
      if (chosen.length >= maxCities) break;
    }
    return chosen.map(c => `<g class="footprint-city"><circle cx="${c.px.toFixed(1)}" cy="${c.py.toFixed(1)}" r="${c.capital ? 3.8 : 3}"></circle><text x="${(c.px + 7).toFixed(1)}" y="${(c.py - 6).toFixed(1)}">${esc(c.name)}</text></g>`).join('');
  }
  async function fetchJson(url) {
    const r = await fetch(url, { cache: 'force-cache' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  function fetchBaseContext() {
    if (!baseContextPromise) {
      baseContextPromise = Promise.allSettled([
        fetchJson(NATURAL_EARTH.coast),
        fetchJson(NATURAL_EARTH.borders),
        fetchJson(NATURAL_EARTH.cities)
      ]).then(results => ({
        coast: results[0].status === 'fulfilled' ? results[0].value : null,
        borders: results[1].status === 'fulfilled' ? results[1].value : null,
        cities: results[2].status === 'fulfilled' ? results[2].value : null
      }));
    }
    return baseContextPromise;
  }
  function renderMap(doc, e, context) {
    const geometry = doc.geometry;
    const centerLon = finite(doc.reported_center?.lon) ? Number(doc.reported_center.lon) : Number(e.lon);
    const centerLat = finite(doc.reported_center?.lat) ? Number(doc.reported_center.lat) : Number(e.lat);
    const pts = collectPoints(geometry, centerLon);
    if (!pts.length) return '';
    let minLon = Math.min(...pts.map(p => p[0])), maxLon = Math.max(...pts.map(p => p[0]));
    let minLat = Math.min(...pts.map(p => p[1])), maxLat = Math.max(...pts.map(p => p[1]));
    let lonSpan = Math.max(maxLon - minLon, 0.01), latSpan = Math.max(maxLat - minLat, 0.01);
    const midLon = (minLon + maxLon) / 2, midLat = (minLat + maxLat) / 2;

    // Deliberately zoom farther out than the event itself so a viewer can orient
    // the footprint against countries and nearby cities.
    const minLonView = 8, minLatView = 5;
    lonSpan = Math.max(lonSpan * 1.75, minLonView);
    latSpan = Math.max(latSpan * 1.75, minLatView);
    minLon = midLon - lonSpan / 2; maxLon = midLon + lonSpan / 2;
    minLat = midLat - latSpan / 2; maxLat = midLat + latSpan / 2;
    if (minLat < -90) { maxLat += -90 - minLat; minLat = -90; }
    if (maxLat > 90) { minLat -= maxLat - 90; maxLat = 90; }
    minLat = Math.max(-90, minLat); maxLat = Math.min(90, maxLat);

    const W = 1040, H = 390, m = { l: 86, r: 32, t: 26, b: 64 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const x = lon => m.l + (lon - minLon) / (maxLon - minLon) * pw;
    const y = lat => m.t + (maxLat - lat) / (maxLat - minLat) * ph;
    const lonViewSpan = maxLon - minLon, latViewSpan = maxLat - minLat;
    const lonStep = niceStep(lonViewSpan), latStep = niceStep(latViewSpan);
    const lonTicks = [], latTicks = [];
    for (let v = Math.ceil(minLon / lonStep) * lonStep; v <= maxLon + 1e-9; v += lonStep) lonTicks.push(v);
    for (let v = Math.ceil(minLat / latStep) * latStep; v <= maxLat + 1e-9; v += latStep) latTicks.push(v);
    const gridLon = lonTicks.map(v => `<line class="footprint-gridline" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${H - m.b}"></line><text class="footprint-axistext" x="${x(v)}" y="${H - 30}" text-anchor="middle">${fmtLon(v)}</text>`).join('');
    const gridLat = latTicks.map(v => `<line class="footprint-gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"></line><text class="footprint-axistext" x="${m.l - 13}" y="${y(v) + 5}" text-anchor="end">${fmtLat(v)}</text>`).join('');
    const path = geometryPath(geometry, centerLon, x, y); if (!path) return '';
    const coast = renderLineLayer(context?.coast, 'footprint-coastline', centerLon, x, y, minLon, maxLon, minLat, maxLat);
    const borders = renderLineLayer(context?.borders, 'footprint-country-border', centerLon, x, y, minLon, maxLon, minLat, maxLat);
    const cities = renderCities(context?.cities, centerLon, x, y, minLon, maxLon, minLat, maxLat, lonViewSpan, latViewSpan);
    const pointLon = normLon(centerLon, centerLon);
    const centerVisible = pointLon >= minLon && pointLon <= maxLon && centerLat >= minLat && centerLat <= maxLat;
    const centerMark = centerVisible ? `<circle class="footprint-center-halo" cx="${x(pointLon)}" cy="${y(centerLat)}" r="9"></circle><circle class="footprint-center" cx="${x(pointLon)}" cy="${y(centerLat)}" r="4.5"></circle>` : '';
    const method = esc(doc.footprint_method || e.footprint?.method || 'mapped event polygon');
    const location = `${fmtLat(centerLat)} · ${fmtLon(centerLon)}`;
    const contextText = (context?.coast || context?.borders || context?.cities) ? 'Natural Earth 1:110m context' : 'Geographic context unavailable';
    return `<div class="footprint-inline-head"><div><h3>Mapped event footprint</h3><p>${method} · zoomed-out regional orientation view</p></div><div class="footprint-meta"><strong>${location}</strong><br>${contextText}</div></div><div class="footprint-map-card"><svg class="footprint-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Mapped event footprint with country borders, nearby cities, latitude and longitude grid"><defs><clipPath id="footprint-plot-clip"><rect x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" rx="10"></rect></clipPath></defs><rect class="footprint-bg" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" rx="10"></rect>${gridLon}${gridLat}<g clip-path="url(#footprint-plot-clip)">${coast}${borders}<path class="footprint-shape" d="${path}" fill-rule="evenodd"></path>${cities}${centerMark}</g><text class="footprint-axislabel" x="${m.l + pw / 2}" y="${H - 7}" text-anchor="middle">Longitude</text><text class="footprint-axislabel" x="21" y="${m.t + ph / 2}" transform="rotate(-90 21 ${m.t + ph / 2})" text-anchor="middle">Latitude</text></svg></div><div class="footprint-note">Blue outline = mapped event footprint; red point = reported event coordinate. Coastlines, international boundaries and selected major cities are orientation aids from Natural Earth 1:110m and are not used for exposure calculations. All exposure calculations use the unsimplified mapped footprint; the simplified blue outline is for browser orientation only.</div>`;
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
    slot.innerHTML = '<div class="footprint-status"><span class="footprint-loading">Loading mapped footprint and regional context…</span></div>';
    try {
      const [doc, context] = await Promise.all([fetchFootprint(ref.path), fetchBaseContext()]); if (token !== renderToken) return;
      const current = document.getElementById(`event-${e.id}`)?.querySelector('.event-footprint-context'); if (!current) return;
      const html = renderMap(doc, e, context); if (!html) { current.remove(); return; } current.innerHTML = html;
    } catch (_) {
      if (token !== renderToken) return;
      // The footprint itself is required; optional Natural Earth failures are handled
      // inside fetchBaseContext and therefore do not reach this branch.
      document.getElementById(`event-${e.id}`)?.querySelector('.event-footprint-context')?.remove();
    }
  }
  const baseRenderCards = renderCards;
  renderCards = function () { baseRenderCards(); renderInlineFootprint(); };
  renderCards();
})();
