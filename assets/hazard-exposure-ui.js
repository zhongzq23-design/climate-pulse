'use strict';

// Hazard-specific UI layer. Exposure means spatial/economic screening, not
// observed harm. Drought deliberately no longer displays population exposure.
(() => {
  const hasNum = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const fmtPop = v => hasNum(v) ? Math.round(Number(v)).toLocaleString() : '–';
  const fmtArea = v => hasNum(v) ? `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} km²` : '–';
  const fmtGDP = v => {
    if (!hasNum(v)) return '–';
    const x = Number(v), ax = Math.abs(x);
    if (ax >= 1e12) return `$${(x / 1e12).toFixed(ax >= 10e12 ? 1 : 2)}T`;
    if (ax >= 1e9) return `$${(x / 1e9).toFixed(ax >= 10e9 ? 1 : 2)}B`;
    if (ax >= 1e6) return `$${(x / 1e6).toFixed(ax >= 10e6 ? 1 : 2)}M`;
    return `$${Math.round(x).toLocaleString()}`;
  };
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

    if (e.type === 'Wildfire' && hasNum(e.burned_area_ha)) rows.push(['Burned area', fmtHa(e.burned_area_ha)]);

    const x = e.exposure || {};
    if (e.type === 'Wildfire') {
      if (hasNum(x.forest_area_in_wildfire_footprint_km2)) rows.push(['Forest within mapped fire footprint', fmtArea(x.forest_area_in_wildfire_footprint_km2)]);
      if (hasNum(x.forest_area_member_sum_km2)) rows.push(['Forest overlap · member sum', fmtArea(x.forest_area_member_sum_km2)]);
      if (hasNum(x.population_direct)) rows.push(['Direct population exposure', `${fmtPop(x.population_direct)} people`]);
      if (hasNum(x.population_5km_ring)) rows.push(['Nearby population (≤5 km)', `${fmtPop(x.population_5km_ring)} people`]);
      if (hasNum(x.population_within_5km)) rows.push(['Total potential population exposure', `${fmtPop(x.population_within_5km)} people`]);
      if (hasNum(x.population_direct_member_sum)) rows.push(['Direct population · member sum', `${fmtPop(x.population_direct_member_sum)} people`]);
      if (hasNum(x.population_within_5km_member_sum)) rows.push(['Total population · member sum', `${fmtPop(x.population_within_5km_member_sum)} people`]);
    } else if (e.type === 'Storm') {
      if (hasNum(x.potential_gdp_exposure_proxy_usd)) rows.push(['Potential GDP exposure proxy · 2024', fmtGDP(x.potential_gdp_exposure_proxy_usd)]);
      if (hasNum(x.population_ts_or_impact_footprint)) rows.push(['Population in TC impact footprint', `${fmtPop(x.population_ts_or_impact_footprint)} people`]);
      if (hasNum(x.population_within_300km_of_center)) rows.push(['Population within 300 km · screening', `${fmtPop(x.population_within_300km_of_center)} people`]);
    } else if (e.type === 'Flood') {
      if (hasNum(x.potential_gdp_exposure_proxy_usd)) rows.push(['Potential GDP exposure proxy · 2024', fmtGDP(x.potential_gdp_exposure_proxy_usd)]);
    } else if (e.type === 'Drought') {
      if (hasNum(x.mapped_footprint_area_km2)) rows.push(['Mapped drought footprint', fmtArea(x.mapped_footprint_area_km2)]);
      if (hasNum(x.land_area_in_footprint_km2)) rows.push(['Land within footprint', fmtArea(x.land_area_in_footprint_km2)]);
      if (hasNum(x.forest_area_in_footprint_km2)) rows.push(['Forest within footprint', fmtArea(x.forest_area_in_footprint_km2)]);
      if (hasNum(x.crop_area_in_footprint_km2)) rows.push(['Crop area within footprint', fmtArea(x.crop_area_in_footprint_km2)]);
    }
    return cellGrid(rows);
  };

  function gdpText(x) {
    if (!hasNum(x.potential_gdp_exposure_proxy_usd)) return '';
    const coverage = hasNum(x.gdp_proxy_population_coverage_pct) ? `; WDI coverage ${Number(x.gdp_proxy_population_coverage_pct).toFixed(0)}% of the country-split population basis` : '';
    return `${fmtGDP(x.potential_gdp_exposure_proxy_usd)} potential GDP exposure proxy using World Bank WDI 2024 GDP per capita × GHSL 2025 residential population inside the mapped footprint${coverage}. It is not observed economic loss or local gridded GDP.`;
  }

  function exposureSummary(e) {
    const x = e?.exposure || {};
    if (e?.type === 'Drought') {
      const bits = [];
      if (hasNum(x.mapped_footprint_area_km2)) bits.push(`${fmtArea(x.mapped_footprint_area_km2)} mapped drought footprint`);
      if (hasNum(x.land_area_in_footprint_km2)) bits.push(`${fmtArea(x.land_area_in_footprint_km2)} land`);
      if (hasNum(x.forest_area_in_footprint_km2)) bits.push(`${fmtArea(x.forest_area_in_footprint_km2)} forest (MODIS 2024)`);
      if (hasNum(x.crop_area_in_footprint_km2)) bits.push(`${fmtArea(x.crop_area_in_footprint_km2)} crop physical area (FAO CROPGRIDS 2020)`);
      if (bits.length) return `Spatial overlap within the GDACS drought polygon: ${bits.join(', ')}. These are potentially exposed areas, not verified ecological or agricultural damage.`;
      return '';
    }
    if (e?.type === 'Wildfire') {
      const bits = [];
      if (hasNum(x.forest_area_in_wildfire_footprint_km2)) bits.push(`${fmtArea(x.forest_area_in_wildfire_footprint_km2)} MODIS-2024 forest within the mapped fire footprint`);
      if (hasNum(x.forest_area_member_sum_km2)) bits.push(`${fmtArea(x.forest_area_member_sum_km2)} forest across member footprints (overlaps may be counted twice)`);
      if (hasNum(x.population_direct) || hasNum(x.population_within_5km)) bits.push(`${fmtPop(x.population_direct)} people in the footprint and ${fmtPop(x.population_5km_ring)} additional people within 5 km`);
      if (hasNum(x.population_within_5km_member_sum)) bits.push(`${fmtPop(x.population_within_5km_member_sum)} population-exposures across member footprint-plus-5-km areas`);
      return bits.length ? `${bits.join('; ')}. Spatial exposure does not mean confirmed forest loss, smoke exposure or observed harm.` : '';
    }
    if (e?.type === 'Storm') {
      const bits = [];
      const gt = gdpText(x); if (gt) bits.push(gt);
      if (hasNum(x.population_ts_or_impact_footprint)) bits.push(`${fmtPop(x.population_ts_or_impact_footprint)} people in the GDACS tropical-cyclone impact footprint`);
      else if (hasNum(x.population_within_300km_of_center)) bits.push(`${fmtPop(x.population_within_300km_of_center)} people within 300 km of the reported centre (proximity screen only)`);
      return bits.join(' ');
    }
    if (e?.type === 'Flood') return gdpText(x);
    return '';
  }

  const baseRenderDetail = renderDetail;
  renderDetail = function () {
    baseRenderDetail();
    const e = chosen();
    const text = exposureSummary(e);
    const p = $('detailPanel');
    if (p && text) p.insertAdjacentHTML('beforeend', `<div class="section-label">Potential exposure</div><div class="climate-box">${esc(text)}</div>`);
  };

  memberHtml = function (e) {
    if (!Array.isArray(e.members) || e.members.length < 2) return '';
    return `<div class="section-label">Grouped reports</div>${e.members.map((m, i) => {
      const x = m.exposure || {};
      const metrics = [];
      if (m.type === 'Wildfire' && hasNum(m.burned_area_ha)) metrics.push(fmtHa(m.burned_area_ha));
      if (hasNum(x.forest_area_in_wildfire_footprint_km2)) metrics.push(`${fmtArea(x.forest_area_in_wildfire_footprint_km2)} forest overlap`);
      if (hasNum(x.population_direct)) metrics.push(`${fmtPop(x.population_direct)} direct population`);
      if (hasNum(x.population_5km_ring)) metrics.push(`${fmtPop(x.population_5km_ring)} nearby ≤5 km`);
      if (hasNum(x.population_within_5km)) metrics.push(`${fmtPop(x.population_within_5km)} total potential population`);
      return `<div class="cell" style="margin-top:7px"><b>${i + 1}. ${esc(m.source)}</b>${esc(m.title)}<br><span class="region">${esc(m.updated)}${metrics.length ? ` · ${esc(metrics.join(' · '))}` : ''}</span></div>`;
    }).join('')}`;
  };
})();
