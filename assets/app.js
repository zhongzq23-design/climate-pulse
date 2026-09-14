'use strict';

const COLORS = {
  Heat: '#e85d04',
  Wildfire: '#c1121f',
  Flood: '#247ba0',
  Drought: '#b58d12',
  Storm: '#6a4c93',
  Landslide: '#8d6e63'
};
const FILTERS = ['All', 'Heat', 'Wildfire', 'Flood', 'Drought', 'Storm', 'Landslide'];
let events = [], mode = 'standard', filter = 'All', selected = null, expanded = null, limit = 12, popupId = null;
const sourceState = {
  eonet: { label: 'NASA EONET', status: 'idle', count: 0 },
  gdacs: { label: 'GDACS', status: 'idle', count: 0 },
  cems: { label: 'Copernicus CEMS', status: 'idle', count: 0 }
};

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

function project(lon, lat) {
  return { x: (+lon + 180) / 360 * 1200, y: (90 - (+lat)) / 180 * 600 };
}
function visible() {
  return mode === 'emerging' ? [] : events.filter(e => filter === 'All' || e.type === filter);
}
function chosen() {
  return visible().find(e => e.id === selected) || visible()[0] || null;
}
function fmtCoord(v, pos, neg) {
  const n = +v;
  return Number.isFinite(n) ? `${Math.abs(n).toFixed(2)}°${n >= 0 ? pos : neg}` : '–';
}
function fmtHa(v) {
  const n = +v;
  return Number.isFinite(n) ? `${Math.round(n).toLocaleString()} ha` : '–';
}
function setSourceStatus(key, status, count = 0, error = '') {
  Object.assign(sourceState[key], { status, count, error });
  renderSourceStatus();
}
function renderSourceStatus() {
  const el = $('sourceStatus');
  if (!el) return;
  el.innerHTML = Object.values(sourceState).map(s =>
    `<span class="${s.status === 'live' ? 'source-ok' : s.status === 'failed' ? 'source-fail' : 'source-loading'}">${esc(s.label)}: ${s.status === 'live' ? `${s.count} relevant` : s.status === 'loading' ? 'loading…' : s.status === 'failed' ? `failed${s.error ? ` · ${esc(s.error)}` : ''}` : 'waiting'}</span>`
  ).join(' &nbsp;·&nbsp; ');
}
function renderModes() {
  $('modeSwitch').querySelectorAll('.mode').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
    b.onclick = () => {
      mode = b.dataset.mode;
      filter = 'All';
      selected = null;
      expanded = null;
      limit = 12;
      render();
    };
  });
  $('mapTitle').textContent = mode === 'standard' ? 'Live Standard Events' : 'Emerging Signals';
  $('mapSubtitle').textContent = mode === 'standard'
    ? 'Authoritative feed candidates. Prime meridian is centered.'
    : 'Editorial layer for less-standardized cryosphere, ecosystem and biodiversity signals.';
  $('feedTitle').textContent = mode === 'standard' ? 'Standard Event Feed' : 'Emerging Signal Review Feed';
  $('feedSubtitle').textContent = mode === 'standard'
    ? 'Expand a card for full source and update details.'
    : 'This layer will be populated only after stronger human scientific review.';
}
function renderFilters() {
  const f = mode === 'standard' ? FILTERS : ['All'];
  $('filters').innerHTML = f.map(x =>
    `<button class="filter ${x === filter ? 'active' : ''}" data-filter="${x}">${x}</button>`
  ).join('');
  $('filters').querySelectorAll('button').forEach(b => b.onclick = () => {
    filter = b.dataset.filter;
    selected = null;
    expanded = null;
    limit = 12;
    render();
  });
}

function renderLegend(v) {
  const wrap = $('mapWrap');
  if (!wrap) return;
  let legend = $('mapLegend');
  if (!legend) {
    legend = document.createElement('div');
    legend.id = 'mapLegend';
    wrap.appendChild(legend);
  }
  if (v.length <= 1) {
    legend.style.display = 'none';
    legend.innerHTML = '';
    return;
  }
  const types = FILTERS.slice(1).filter(type => v.some(e => e.type === type));
  const hasCluster = v.some(e => +e.member_count > 1);
  legend.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:9px 14px;padding:10px 4px 2px;color:#617789;font-size:12px;line-height:1.2';
  const items = types.map(type => {
    const color = COLORS[type] || '#1f6aa5';
    return `<span style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap"><span aria-hidden="true" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};box-shadow:0 0 0 3px ${color}22"></span>${esc(type)}</span>`;
  }).join('');
  const grouped = hasCluster
    ? '<span style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap"><span aria-hidden="true" style="display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:#c1121f;color:#fff;font-weight:800;font-size:10px;box-shadow:0 0 0 4px #c1121f22">3</span>Number = grouped reports</span>'
    : '';
  legend.innerHTML = `<strong style="color:#12263a;font-size:12px">Legend</strong>${items}${grouped}`;
}

function renderMap() {
  const v = visible(), g = $('markers');
  $('visibleCount').textContent = `${v.length} event${v.length === 1 ? '' : 's'}`;
  g.innerHTML = v.filter(e => Number.isFinite(+e.lon) && Number.isFinite(+e.lat)).map(e => {
    const p = project(e.lon, e.lat), c = COLORS[e.type] || '#1f6aa5', cluster = +e.member_count > 1, sel = e.id === selected;
    return `<g class="marker ${sel ? 'selected' : ''}" tabindex="0" data-id="${esc(e.id)}" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})"><circle class="halo" r="${cluster ? 22 : 18}" fill="${c}"></circle><circle class="dot" r="${sel ? 10 : 8}" fill="${c}"></circle>${cluster ? `<text>${Math.min(99, e.member_count)}</text>` : ''}<title>${esc(e.title)}</title></g>`;
  }).join('');
  g.querySelectorAll('.marker').forEach(m => {
    const fn = () => selectMap(m.dataset.id);
    m.onclick = fn;
    m.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fn();
      }
    };
  });
  renderLegend(v);
}
function selectMap(id) {
  selected = id;
  popupId = id;
  renderMap();
  renderDetail();
  renderCards();
  requestAnimationFrame(() => showPopup(id));
}
function showPopup(id) {
  const e = events.find(x => x.id === id);
  if (!e) return;
  const p = project(e.lon, e.lat), svg = $('worldMap'), wrap = $('mapWrap'), sr = svg.getBoundingClientRect(), wr = wrap.getBoundingClientRect(), box = $('mapPopup');
  let left = sr.left - wr.left + p.x / 1200 * sr.width, top = sr.top - wr.top + p.y / 600 * sr.height;
  left = Math.max(155, Math.min(wrap.clientWidth - 155, left));
  top = Math.max(110, top);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  box.innerHTML = `<div class="popup-type" style="color:${COLORS[e.type] || '#1f6aa5'}">${esc(e.type)} · ${esc(e.status)}</div><div class="popup-title">${esc(e.title)}</div><div class="popup-meta">${esc(e.region)}<br>${esc(e.source)} · ${esc(e.updated)}</div><div class="popup-actions"><button class="view-event">View event ↓</button><button class="close-popup">Close</button></div>`;
  box.classList.add('open');
  box.querySelector('.view-event').onclick = () => scrollEvent(id);
  box.querySelector('.close-popup').onclick = closePopup;
}
function closePopup() {
  const p = $('mapPopup');
  p.classList.remove('open');
  p.innerHTML = '';
  popupId = null;
}
function scrollEvent(id) {
  selected = id;
  expanded = id;
  const v = visible(), i = v.findIndex(x => x.id === id);
  if (i >= limit) limit = i + 1;
  render();
  closePopup();
  requestAnimationFrame(() => {
    const target = document.getElementById(`event-${id}`);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}
function typeTag(e) {
  return `<span class="tag type-tag" style="background:${COLORS[e.type] || '#1f6aa5'}">${esc(e.type)}</span>`;
}
function sourceLinks(e) {
  const u = [...new Set([...(e.source_urls || []), e.source_url, ...(e.members || []).map(x => x.source_url)].filter(Boolean))];
  return u.length
    ? `<div class="source-list">${u.slice(0, 8).map((x, i) => `<a href="${esc(x)}" target="_blank" rel="noopener">Open source ${i + 1} ↗</a>`).join('')}</div>`
    : '<p>No direct source URL available.</p>';
}
function detailCells(e) {
  const rows = [
    ['Event type', e.type],
    ['Status', e.status],
    ['Latest update', e.updated],
    ['Coordinates', `${fmtCoord(e.lat, 'N', 'S')}, ${fmtCoord(e.lon, 'E', 'W')}`],
    ['Source', e.source],
    ['Source ID', e.source_id || '–']
  ];
  if (Number.isFinite(+e.burned_area_ha)) rows.push(['Burned area', fmtHa(e.burned_area_ha)]);
  return `<div class="detail-grid">${rows.map(r => `<div class="cell"><b>${esc(r[0])}</b>${esc(r[1])}</div>`).join('')}</div>`;
}
function renderDetail() {
  const e = chosen(), p = $('detailPanel');
  if (!e) {
    p.innerHTML = `<div class="empty">${mode === 'emerging' ? 'Emerging Signals are intentionally empty in the public bootstrap until the review workflow is implemented.' : 'No event matches this filter.'}</div>`;
    return;
  }
  selected = e.id;
  p.innerHTML = `${typeTag(e)}<span class="tag">${esc(e.status)}</span><span class="tag">${esc(e.source)}</span>${+e.member_count > 1 ? `<span class="tag">${e.member_count} reports grouped</span>` : ''}<h2>${esc(e.title)}</h2><div class="region">${esc(e.region)} · ${esc(e.updated)}</div><div class="summary">${esc(e.summary)}</div>${e.source_url ? `<a class="source-link" href="${esc(e.source_url)}" target="_blank" rel="noopener">Open primary source ↗</a>` : ''}<div class="section-label">Local warming context</div><div class="climate-box">Reserved for CRU/ERA5 annual or 5-year temperature context using this event’s coordinates. <strong>Climate context is not event attribution.</strong></div>`;
}
function memberHtml(e) {
  if (!Array.isArray(e.members) || e.members.length < 2) return '';
  return `<div class="section-label">Grouped reports</div>${e.members.map((m, i) => `<div class="cell" style="margin-top:7px"><b>${i + 1}. ${esc(m.source)}</b>${esc(m.title)}<br><span class="region">${esc(m.updated)}${Number.isFinite(+m.burned_area_ha) ? ` · ${fmtHa(m.burned_area_ha)}` : ''}</span></div>`).join('')}`;
}
function renderCards() {
  const v = visible(), shown = v.slice(0, limit);
  $('feedCount').textContent = `${v.length} event${v.length === 1 ? '' : 's'}`;
  $('eventList').innerHTML = shown.map(e => {
    const ex = e.id === expanded;
    return `<article class="event-card ${e.id === selected ? 'selected' : ''} ${ex ? 'expanded' : ''}" id="event-${esc(e.id)}" data-id="${esc(e.id)}">${typeTag(e)}${+e.member_count > 1 ? `<span class="tag">${e.member_count} reports grouped</span>` : ''}<h3>${esc(e.title)}</h3><p>${esc(e.region)}</p><p style="margin-top:7px">${esc(String(e.summary || '').slice(0, 118))}${String(e.summary || '').length > 118 && !ex ? '…' : ''}</p><div class="event-meta">${esc(e.source)} · ${esc(e.updated)}</div><button class="details-toggle">${ex ? 'Hide details ↑' : 'Show full details ↓'}</button>${ex ? `<div class="event-details">${detailCells(e)}<div class="section-label">Source links</div>${sourceLinks(e)}${memberHtml(e)}<div class="climate-box">Climate link: ${esc(e.climate_link || 'Not assessed')}. Feed presence does not establish climate attribution.</div></div>` : ''}</article>`;
  }).join('');
  $('eventList').querySelectorAll('.event-card').forEach(c => {
    c.onclick = e => {
      if (e.target.closest('a') || e.target.closest('button')) return;
      selected = c.dataset.id;
      closePopup();
      renderMap();
      renderDetail();
      renderCards();
    };
    c.querySelector('button').onclick = e => {
      e.stopPropagation();
      selected = c.dataset.id;
      expanded = expanded === c.dataset.id ? null : c.dataset.id;
      render();
    };
  });
  const remain = v.length - shown.length;
  $('loadMore').innerHTML = remain > 0 ? `<button>Show ${Math.min(12, remain)} more · ${remain} remaining</button>` : '';
  $('loadMore').querySelector('button')?.addEventListener('click', () => {
    limit += 12;
    renderCards();
  });
}
function render() {
  renderModes();
  renderFilters();
  renderMap();
  renderDetail();
  renderCards();
}
async function refresh() {
  const b = $('refreshBtn');
  b.disabled = true;
  b.textContent = 'Refreshing…';
  const snap = await cpLoadRepositorySnapshot();
  if (snap) {
    events = snap.events;
    $('lastUpdated').textContent = `Repository snapshot · ${snap.generated_at ? new Date(snap.generated_at).toLocaleString() : 'versioned data'}`;
    Object.keys(sourceState).forEach(k => setSourceStatus(k, 'live', events.filter(e => e.origin === k).length));
  } else {
    const out = await cpLoadLive(setSourceStatus);
    events = out.events;
    $('lastUpdated').textContent = `Live feeds · ${new Date(out.generated_at).toLocaleString()}`;
  }
  selected = events[0]?.id || null;
  expanded = null;
  limit = 12;
  b.disabled = false;
  b.textContent = 'Refresh';
  render();
}

$('refreshBtn').onclick = refresh;
document.addEventListener('click', e => {
  const p = $('mapPopup');
  if (p.classList.contains('open') && !p.contains(e.target) && !e.target.closest('.marker')) closePopup();
});
window.addEventListener('resize', () => {
  if (popupId) showPopup(popupId);
});
renderSourceStatus();
render();
refresh();
