'use strict';

(() => {
  const baseDetailCells = detailCells;

  const fmtDate = value => {
    const s = String(value || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return String(value || '–');
    const d = new Date(`${s}T00:00:00Z`);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
  };

  function metricSource(e, key) {
    const x = e?.exposure || {};
    return (x.metric_provenance && x.metric_provenance[key]) ||
      (e?.source_metrics?.metric_provenance && e.source_metrics.metric_provenance[key]) || null;
  }

  function sourceLabel(meta, fallback) {
    if (!meta) return fallback;
    const src = String(meta.source || fallback || 'Source');
    if (meta.derived_by_climate_pulse) return `${src} · derived estimate`;
    if (String(meta.method || '').includes('modelled')) return `${src} · modelled exposure`;
    if (String(meta.method || '').includes('current_episode')) {
      const ep = meta.gdacs_episode_id ? ` · episode ${meta.gdacs_episode_id}` : '';
      return `${src} · current episode${ep}`;
    }
    return src;
  }

  function appendCell(afterNode, label, value, meta) {
    const cell = document.createElement('div');
    cell.className = 'cell metric-source-aligned';
    const b = document.createElement('b');
    b.textContent = label;
    cell.appendChild(b);
    cell.appendChild(document.createTextNode(value));
    if (meta) {
      const span = document.createElement('span');
      span.className = 'metric-source';
      span.textContent = meta;
      cell.appendChild(span);
    }
    afterNode.insertAdjacentElement('afterend', cell);
    return cell;
  }

  detailCells = function (e) {
    const html = baseDetailCells(e);
    if (!e || e.type !== 'Wildfire') return html;

    const template = document.createElement('template');
    template.innerHTML = html;
    const cells = [...template.content.querySelectorAll('.cell')];
    const byLabel = label => cells.find(c => c.querySelector('b')?.textContent.trim() === label);

    const burned = byLabel('Burned area');
    if (burned) {
      const meta = burned.querySelector('.metric-source') || burned.appendChild(document.createElement('span'));
      meta.className = 'metric-source';
      meta.textContent = sourceLabel(metricSource(e, 'burned_area_ha'), 'GDACS/GWIS · current episode');

      let anchor = burned;
      const s = e.source_metrics || {};
      if (s.wildfire_last_detection) {
        anchor = appendCell(
          anchor,
          'Last detection',
          fmtDate(s.wildfire_last_detection),
          `GDACS/GWIS · episode ${s.wildfire_current_episode_id || 'current'}`
        );
      }
      if (Number.isFinite(Number(s.wildfire_duration_days))) {
        anchor = appendCell(
          anchor,
          'Duration',
          `${Math.round(Number(s.wildfire_duration_days))} days`,
          `GDACS/GWIS · episode ${s.wildfire_current_episode_id || 'current'}`
        );
      }
    }

    const direct = byLabel('Population exposed in burned area');
    if (direct) {
      const b = direct.querySelector('b');
      if (b) b.textContent = 'People in burned area';
      const meta = direct.querySelector('.metric-source') || direct.appendChild(document.createElement('span'));
      meta.className = 'metric-source';
      meta.textContent = `GDACS page label: “People affected” · ${sourceLabel(metricSource(e, 'population_direct'), 'GDACS/GWIS modelled exposure')}`;
    }

    return template.innerHTML;
  };
})();
