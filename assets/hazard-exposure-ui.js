'use strict';

// Source-first hazard UI. Upstream GDACS/GDO/GWIS metrics are preferred.
// Climate Pulse-derived quantities are labelled fallbacks or supplemental
// spatial context. Exposure never means observed harm.
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

  function alertLevel(e) {
    const explicit = String(e?.alert_level || '').toLowerCase();
    if (['green', 'orange', 'red'].includes(explicit)) return explicit[0].toUpperCase() + explicit.slice(1);
    const text = `${e?.source || ''} ${e?.summary || ''}`.toLowerCase();
    if (/\bred\b/.test(text)) return 'Red';
    if (/\borange\b/.test(text)) return 'Orange';
    if (/\bgreen\b/.test(text)) return 'Green';
    return '';
  }

  function provenance(e, key) {
    const x = e?.exposure || {};
    return (x.metric_provenance && x.metric_provenance[key]) ||
      (e?.source_metrics?.metric_provenance && e.source_metrics.metric_provenance[key]) || null;
  }
  function sourceText(meta, fallback = '') {
    if (!meta) return fallback;
    const src = String(meta.source || fallback || 'Source');
    if (meta.derived_by_climate_pulse) return `${src} · derived estimate`;
    if (String(meta.method || '').includes('modelled')) return `${src} · modelled exposure`;
    return src;
  }
  const row = (label, value, meta = '', tone = '') => ({ label, value, meta, tone });
  function cellGrid(rows) {
    return `<div class="detail-grid source-first-grid">${rows.map(r =>
      `<div class="cell ${r.tone ? `metric-${esc(r.tone)}` : ''}"><b>${esc(r.label)}</b>${esc(r.value)}${r.meta ? `<span class="metric-source">${esc(r.meta)}</span>` : ''}</div>`
    ).join('')}</div>`;
  }
  const sourceMetric = (e, key) => e?.source_metrics?.[key];

  function humanImpactRows(e) {
    if (alertLevel(e) !== 'Red') return [];
    const h = e?.source_metrics?.human_impact || {}, x = e?.exposure || {}, rows = [];
    const candidates = [
      ['Affected population', h.affected_population, 'Source-reported actual impact'],
      ['Displaced population', h.displaced_population, 'Source-reported actual impact'],
      ['Fatalities', h.fatalities, 'Source-reported actual impact'],
      ['People facing food insecurity', h.food_insecurity_population, 'Source-reported actual impact'],
      ['Estimated population exposed', x.estimated_population_exposed, sourceText(provenance(e, 'estimated_population_exposed'), 'Climate Pulse · derived estimate')],
    ];
    candidates.forEach(([label, value, meta]) => { if (hasNum(value)) rows.push(row(label, `${fmtPop(value)} people`, meta, 'human')); });
    if (h.water_shortage) rows.push(row('Water shortage', String(h.water_shortage), 'Source-reported actual impact', 'human'));
    if (h.humanitarian_assistance) rows.push(row('Humanitarian assistance', String(h.humanitarian_assistance), 'Source-reported actual impact', 'human'));
    return rows;
  }

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
  renderModes = function () { baseRenderModes(); renderSourceStatus(); };

  detailCells = function (e) {
    const rows = [
      row('Event type', e.type), row('Status', e.status), row('Latest update', e.updated),
      row('Coordinates', `${fmtCoord(e.lat, 'N', 'S')}, ${fmtCoord(e.lon, 'E', 'W')}`),
      row('Source', e.source), row('Source ID', e.source_id || '–')
    ];
    const level = alertLevel(e), x = e.exposure || {};
    if (level) rows.push(row('GDACS alert level', level, 'Severity / alert classification'));
    else if (e.priority) rows.push(row('Priority', e.priority));

    if (e.type === 'Wildfire') {
      if (hasNum(e.burned_area_ha)) rows.push(row('Burned area', fmtHa(e.burned_area_ha), 'GDACS event metric'));
      if (hasNum(x.population_direct)) rows.push(row('Population exposed in burned area', `${fmtPop(x.population_direct)} people`, sourceText(provenance(e, 'population_direct')), 'source'));
      for (const [key, label] of [
        ['population_within_1km', 'Population within 1 km'], ['population_within_2km', 'Population within 2 km'],
        ['population_within_5km', 'Population within 5 km'], ['population_within_10km', 'Population within 10 km']
      ]) if (hasNum(x[key])) rows.push(row(label, `${fmtPop(x[key])} people`, sourceText(provenance(e, key)), 'source'));
      if (hasNum(x.population_direct_member_sum)) rows.push(row('Cluster population · burned-area member sum', `${fmtPop(x.population_direct_member_sum)} people`, 'Display sum; member footprints can overlap'));
      if (hasNum(x.population_within_5km_member_sum)) rows.push(row('Cluster population · ≤5 km member sum', `${fmtPop(x.population_within_5km_member_sum)} people`, 'Display sum; not a unique-population count'));
      if (hasNum(x.forest_area_in_wildfire_footprint_km2)) rows.push(row('Supplemental · forest within mapped fire footprint', fmtArea(x.forest_area_in_wildfire_footprint_km2), 'Climate Pulse · MODIS 2024 spatial overlap', 'supplemental'));
      if (hasNum(x.forest_area_member_sum_km2)) rows.push(row('Supplemental · forest member sum', fmtArea(x.forest_area_member_sum_km2), 'Climate Pulse · overlaps may be counted twice', 'supplemental'));
    } else if (e.type === 'Storm') {
      if (hasNum(x.population_wind_39kt)) rows.push(row('Population in ≥39 kt wind field', `${fmtPop(x.population_wind_39kt)} people`, sourceText(provenance(e, 'population_wind_39kt')), 'source'));
      if (hasNum(x.population_wind_74kt)) rows.push(row('Population in ≥74 kt wind field', `${fmtPop(x.population_wind_74kt)} people`, sourceText(provenance(e, 'population_wind_74kt')), 'source'));
      if (hasNum(x.population_storm_surge)) rows.push(row('Population in storm-surge zone', `${fmtPop(x.population_storm_surge)} people`, sourceText(provenance(e, 'population_storm_surge')), 'source'));
      if (hasNum(x.population_ts_or_impact_footprint)) rows.push(row('Estimated population in mapped TC footprint', `${fmtPop(x.population_ts_or_impact_footprint)} people`, sourceText(provenance(e, 'population_ts_or_impact_footprint'), 'Climate Pulse · fallback'), 'fallback'));
      if (hasNum(x.population_within_300km_of_center)) rows.push(row('Population within 300 km · screening only', `${fmtPop(x.population_within_300km_of_center)} people`, sourceText(provenance(e, 'population_within_300km_of_center'), 'Climate Pulse · fallback'), 'fallback'));
      if (hasNum(x.potential_gdp_exposure_proxy_usd)) rows.push(row('Supplemental · potential GDP exposure proxy', fmtGDP(x.potential_gdp_exposure_proxy_usd), 'Climate Pulse · WDI 2024 × GHSL 2025; not economic loss', 'supplemental'));
    } else if (e.type === 'Flood') {
      if (hasNum(x.potential_gdp_exposure_proxy_usd)) rows.push(row('Supplemental · potential GDP exposure proxy', fmtGDP(x.potential_gdp_exposure_proxy_usd), 'Climate Pulse · WDI 2024 × GHSL 2025; not economic loss', 'supplemental'));
    } else if (e.type === 'Drought') {
      const agArea = sourceMetric(e, 'agricultural_drought_impact_area_km2');
      if (hasNum(agArea)) rows.push(row('Agricultural drought potential-impact/risk area', fmtArea(agArea), 'GDACS / Global Drought Observatory · source published', 'source'));
      if (hasNum(x.mapped_footprint_area_km2)) rows.push(row('Supplemental · mapped drought footprint', fmtArea(x.mapped_footprint_area_km2), 'Climate Pulse spatial context; not damage area', 'supplemental'));
      if (level === 'Orange' || level === 'Red') {
        if (hasNum(x.land_area_in_footprint_km2)) rows.push(row('Supplemental · land within footprint', fmtArea(x.land_area_in_footprint_km2), 'Climate Pulse spatial overlap', 'supplemental'));
        if (hasNum(x.forest_area_in_footprint_km2)) rows.push(row('Supplemental · forest within footprint', fmtArea(x.forest_area_in_footprint_km2), 'Climate Pulse · MODIS 2024 spatial overlap', 'supplemental'));
        if (hasNum(x.crop_area_in_footprint_km2)) rows.push(row('Supplemental · crop physical area within footprint', fmtArea(x.crop_area_in_footprint_km2), 'Climate Pulse · FAO CROPGRIDS 2020 spatial overlap; not crop loss', 'supplemental'));
      }
    }
    return cellGrid(rows);
  };

  function gdpText(x) {
    if (!hasNum(x.potential_gdp_exposure_proxy_usd)) return '';
    const coverage = hasNum(x.gdp_proxy_population_coverage_pct) ? `; WDI coverage ${Number(x.gdp_proxy_population_coverage_pct).toFixed(0)}% of the country-split population basis` : '';
    return `${fmtGDP(x.potential_gdp_exposure_proxy_usd)} supplemental GDP-exposure proxy using World Bank WDI 2024 GDP per capita × GHSL 2025 residential population inside the mapped footprint${coverage}. It is not observed economic loss.`;
  }
  function exposureSummary(e) {
    const x = e?.exposure || {}, level = alertLevel(e);
    if (e?.type === 'Drought') {
      const agArea = sourceMetric(e, 'agricultural_drought_impact_area_km2'), bits = [];
      if (hasNum(agArea)) bits.push(`${fmtArea(agArea)} agricultural drought potential-impact/risk area from GDACS/GDO`);
      if (hasNum(x.mapped_footprint_area_km2)) bits.push(`${fmtArea(x.mapped_footprint_area_km2)} mapped footprint as supplemental spatial context`);
      const tail = level === 'Red' ? 'Human-impact numbers are shown only when a reliable source reports them; any Climate Pulse estimate is explicitly labelled.' : 'Population and humanitarian-impact figures are not promoted for this event unless the alert becomes Red and reliable data are available.';
      return bits.length ? `${bits.join('; ')}. This is not confirmed crop loss. ${tail}` : tail;
    }
    if (e?.type === 'Wildfire') {
      const bits = [];
      if (hasNum(x.population_direct)) bits.push(`${fmtPop(x.population_direct)} people exposed in the burned area`);
      if (hasNum(x.population_within_5km)) bits.push(`${fmtPop(x.population_within_5km)} people within 5 km`);
      if (hasNum(x.population_within_10km)) bits.push(`${fmtPop(x.population_within_10km)} people within 10 km`);
      return bits.length ? `${bits.join('; ')}. ${sourceText(provenance(e, 'population_direct'), 'Climate Pulse fallback')}. Exposure is not confirmed harm, smoke dose, evacuation or casualty count.` : '';
    }
    if (e?.type === 'Storm') {
      const bits = [];
      if (hasNum(x.population_wind_39kt)) bits.push(`${fmtPop(x.population_wind_39kt)} people in the ≥39 kt wind field`);
      if (hasNum(x.population_wind_74kt)) bits.push(`${fmtPop(x.population_wind_74kt)} in the ≥74 kt wind field`);
      if (hasNum(x.population_storm_surge)) bits.push(`${fmtPop(x.population_storm_surge)} in the storm-surge zone`);
      if (!bits.length && hasNum(x.population_ts_or_impact_footprint)) bits.push(`${fmtPop(x.population_ts_or_impact_footprint)} people in a Climate Pulse fallback footprint estimate`);
      const gt = gdpText(x);
      return `${bits.length ? `${bits.join('; ')}. Source exposure is modelled, not observed harm.` : ''}${gt ? ` ${gt}` : ''}`.trim();
    }
    if (e?.type === 'Flood') return gdpText(x);
    return '';
  }

  const baseRenderDetail = renderDetail;
  renderDetail = function () {
    baseRenderDetail();
    const e = chosen(), p = $('detailPanel');
    if (!p || !e) return;
    const text = exposureSummary(e);
    if (text) p.insertAdjacentHTML('beforeend', `<div class="section-label">Source-first exposure</div><div class="climate-box source-first-box">${esc(text)}</div>`);
    const human = humanImpactRows(e);
    if (alertLevel(e) === 'Red') {
      if (human.length) p.insertAdjacentHTML('beforeend', `<div class="section-label">Human impact · Red alert</div>${cellGrid(human)}`);
      else p.insertAdjacentHTML('beforeend', '<div class="section-label">Human impact · Red alert</div><div class="climate-box source-first-box">No reliable structured human-impact count is available in the current event record. Climate Pulse does not invent an affected-population number.</div>');
    }
  };

  memberHtml = function (e) {
    if (!Array.isArray(e.members) || e.members.length < 2) return '';
    return `<div class="section-label">Grouped reports</div>${e.members.map((m, i) => {
      const x = m.exposure || {}, metrics = [];
      if (m.type === 'Wildfire' && hasNum(m.burned_area_ha)) metrics.push(fmtHa(m.burned_area_ha));
      if (hasNum(x.population_direct)) metrics.push(`${fmtPop(x.population_direct)} exposed in burned area`);
      if (hasNum(x.population_within_5km)) metrics.push(`${fmtPop(x.population_within_5km)} within 5 km`);
      if (hasNum(x.population_within_10km)) metrics.push(`${fmtPop(x.population_within_10km)} within 10 km`);
      const src = sourceText(provenance(m, 'population_direct'));
      return `<div class="cell" style="margin-top:7px"><b>${i + 1}. ${esc(m.source)}</b>${esc(m.title)}<br><span class="region">${esc(m.updated)}${metrics.length ? ` · ${esc(metrics.join(' · '))}` : ''}${src ? ` · ${esc(src)}` : ''}</span></div>`;
    }).join('')}`;
  };
})();
