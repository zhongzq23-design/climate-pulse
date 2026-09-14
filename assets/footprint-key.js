'use strict';

// Keep the compact footprint legend focused on what readers need to identify
// the visible map elements. Methodological and calculation details remain on
// the Methods page rather than in the map key.
(() => {
  function rewriteMapKeys(root = document) {
    const notes = root.querySelectorAll ? root.querySelectorAll('.footprint-note') : [];
    notes.forEach(note => {
      const current = note.textContent || '';
      const isFlood = current.includes('For floods,');
      const floodText = isFlood
        ? ' For floods, the blue shape is the GDACS reported affected-area/event polygon, not observed inundation extent.'
        : '';
      note.innerHTML = '<strong>Map key:</strong> blue fill/outline = mapped event footprint; red point = reported event coordinate; grey solid/dashed lines = coastlines and international boundaries; dots/labels = selected major cities.' + floodText;
    });
  }

  function start() {
    rewriteMapKeys();
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (!(node instanceof Element)) continue;
          if (node.matches?.('.footprint-note') || node.querySelector?.('.footprint-note')) {
            rewriteMapKeys(node.matches?.('.footprint-note') ? node.parentElement || document : node);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
