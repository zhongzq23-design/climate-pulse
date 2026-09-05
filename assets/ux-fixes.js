'use strict';

// Small compatibility layer while the prototype is being refactored.
// 1) Never show a burned-area field for non-wildfire hazards.
// 2) Make it clear that Emerging Signals do not yet have a live ingestion/review feed.

detailCells = function (e) {
  const rows = [
    ['Event type', e.type],
    ['Status', e.status],
    ['Latest update', e.updated],
    ['Coordinates', `${fmtCoord(e.lat, 'N', 'S')}, ${fmtCoord(e.lon, 'E', 'W')}`],
    ['Source', e.source],
    ['Source ID', e.source_id || '–'],
    ['Priority', e.priority || 'Standard']
  ];

  const ha = e.burned_area_ha;
  if (e.type === 'Wildfire' && ha !== null && ha !== undefined && ha !== '' && Number.isFinite(Number(ha))) {
    rows.push(['Burned area', fmtHa(ha)]);
  }

  return `<div class="detail-grid">${rows.map(r => `<div class="cell"><b>${esc(r[0])}</b>${esc(r[1])}</div>`).join('')}</div>`;
};

const cpBaseRenderSourceStatus = renderSourceStatus;
renderSourceStatus = function () {
  const el = $('sourceStatus');
  if (!el) return;
  if (mode === 'emerging') {
    el.innerHTML = '<span class="source-loading">Emerging Signals: live candidate/review pipeline not enabled yet.</span>';
    return;
  }
  cpBaseRenderSourceStatus();
};

const cpBaseRenderModes = renderModes;
renderModes = function () {
  cpBaseRenderModes();
  renderSourceStatus();
};

render();
