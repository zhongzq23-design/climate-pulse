'use strict';

// Hazard-specific UI layer. Kept separate from app.js so the exposure pipeline can
// evolve without coupling ingestion logic to the base map interactions.
(() => {
  const hasNum = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const fmtPop = v => hasNum(v) ? Math.round(Number(v)).toLocaleString() : '–';
  const cellGrid = rows => `<div class="detail-grid">${rows.map(r => `<div class="cell"><b>${esc(r[0])}</b>${esc(r[1])}</div>`).join('')}</div>`;

  const baseRenderSourceStatus = renderSourceStatus;
  renderSourceStatus = function () {
    if (mode === 'emerging') {
      const el = $('sourceStatus');
      if (el) el.innerHTML = '<span class="source-loading">Emerging Signals: live candidate/review pipeline not enabled yet.</span>';
      return;
    }
    baseRenderSourceStatus();
  };

  const baseRenderModes = renderModes;
  renderModes = function () {
    baseRenderModes();
    renderSourceStatus();
  };

  detailCells = function (e) {
    const rows = [
      ['Event type', e.type],
      ['Status', e.status],
      ['Latest update', e.updated],
      ['Coordinates', `${fmtCoord(e.lat, 'N', 'S')}, ${fmtCoord(e.lon, 'E', 'W')}`],
      ['Source', e.source],
      ['Source ID', e.source_id || '–']
    ];
    if (e.priority) rows.push(['Priority', e.priority]);

    // Burned area belongs only to wildfire records.
    if (e.type === 'Wildfire' && hasNum(e.burned_area_ha)) {
      rows.push(['Burned area', fmtHa(e.burned_area_ha)]);
    }

    const x = e.exposure || {};
    if (e.type === 'Wildfire') {
      if (hasNum(x.population_direct)) rows.push(['Direct exposure', `${fmtPop(x.population_direct)} people`]);
      if (hasNum(x.population_5km_ring)) rows.push(['Nearby exposure (≤5 km)', `${fmtPop(x.population_5km_ring)} people`]);
      if (hasNum(x.population_within_5km)) rows.push(['Total potential exposure', `${fmtPop(x.population_within_5km)} people`]);
      if (hasNum(x.population_direct_member_sum)) rows.push(['Direct exposure · member sum', `${fmtPop(x.population_direct_member_sum)} people`]);
      if (hasNum(x.population_within_5km_member_sum)) rows.push(['Total exposure · member sum', `${fmtPop(x.population_within_5km_member_sum)} people`]);
    } else if (e.type === 'Storm') {
      if (hasNum(x.population_ts_or_impact_footprint)) rows.push(['Population in TC impact footprint', `${fmtPop(x.population_ts_or_impact_footprint)} people`]);
      if (hasNum(x.population_within_300km_of_center)) rows.push(['Population within 300 km · screening', `${fmtPop(x.population_within_300km_of_center)} people`]);
    }
    return cellGrid(rows);
  };

  function exposureSummary(e) {
    const x = e?.exposure || {};
    if (e?.type === 'Wildfire') {
      if (hasNum(x.population_direct) || hasNum(x.population_within_5km)) {
        const direct = fmtPop(x.population_direct);
        const nearby = fmtPop(x.population_5km_ring);
        const total = fmtPop(x.population_within_5km);
        return `GHSL 2025 population screening: ${direct} people in the mapped wildfire footprint (direct exposure), ${nearby} additional people within 5 km (nearby exposure), and ${total} people in total across the footprint plus 5 km. Exposure does not mean observed harm.`;
      }
      if (hasNum(x.population_within_5km_member_sum)) {
        return `Grouped-fire screening: ${fmtPop(x.population_within_5km_member_sum)} population-exposures across member footprint-plus-5-km areas. Member areas may overlap, so this is not a unique-person count.`;
      }
    }
    if (e?.type === 'Storm') {
      if (hasNum(x.population_ts_or_impact_footprint)) {
        return `GHSL 2025 screening within the GDACS tropical-cyclone impact footprint: ${fmtPop(x.population_ts_or_impact_footprint)} people. This is potential exposure, not damage.`;
      }
      if (hasNum(x.population_within_300km_of_center)) {
        return `Fallback screening: ${fmtPop(x.population_within_300km_of_center)} people live within 300 km of the reported storm centre. This is a proximity screen, not a wind-impact estimate.`;
      }
    }
    return '';
  }

  const baseRenderDetail = renderDetail;
  renderDetail = function () {
    baseRenderDetail();
    const e = chosen();
    const text = exposureSummary(e);
    const p = $('detailPanel');
    if (p && text) {
      p.insertAdjacentHTML('beforeend', `<div class="section-label">Potential population exposure</div><div class="climate-box">${esc(text)}</div>`);
    }
  };

  memberHtml = function (e) {
    if (!Array.isArray(e.members) || e.members.length < 2) return '';
    return `<div class="section-label">Grouped reports</div>${e.members.map((m, i) => {
      const x = m.exposure || {};
      const metrics = [];
      if (m.type === 'Wildfire' && hasNum(m.burned_area_ha)) metrics.push(fmtHa(m.burned_area_ha));
      if (hasNum(x.population_direct)) metrics.push(`${fmtPop(x.population_direct)} direct`);
      if (hasNum(x.population_5km_ring)) metrics.push(`${fmtPop(x.population_5km_ring)} nearby ≤5 km`);
      if (hasNum(x.population_within_5km)) metrics.push(`${fmtPop(x.population_within_5km)} total potential`);
      return `<div class="cell" style="margin-top:7px"><b>${i + 1}. ${esc(m.source)}</b>${esc(m.title)}<br><span class="region">${esc(m.updated)}${metrics.length ? ` · ${esc(metrics.join(' · '))}` : ''}</span></div>`;
    }).join('')}`;
  };
})();
