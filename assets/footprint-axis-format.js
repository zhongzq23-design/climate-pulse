'use strict';

// Presentation-only formatter for coordinate tick labels in the compact
// footprint map. Keep the event-coordinate summary at its existing precision;
// only SVG axis ticks are reduced to one decimal place.
(() => {
  const SELECTOR = '.footprint-axistext';

  function formatAxisTick(node) {
    if (!(node instanceof Element) || !node.matches(SELECTOR)) return;
    const raw = String(node.textContent || '').trim();
    const match = raw.match(/^(\d+(?:\.\d+)?)°\s*([NSEW])$/i);
    if (!match) return;
    const value = Number(match[1]);
    if (!Number.isFinite(value)) return;
    node.textContent = `${value.toFixed(1)}° ${match[2].toUpperCase()}`;
  }

  function formatWithin(root) {
    if (!(root instanceof Element || root instanceof Document)) return;
    if (root instanceof Element && root.matches(SELECTOR)) formatAxisTick(root);
    root.querySelectorAll?.(SELECTOR).forEach(formatAxisTick);
  }

  formatWithin(document);

  const observer = new MutationObserver(records => {
    records.forEach(record => {
      record.addedNodes.forEach(node => {
        if (node instanceof Element) formatWithin(node);
      });
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
