'use strict';

// Adds a compact explanatory link beside the VPD chart title without changing
// the underlying climate-context rendering/data logic.
(() => {
  function applyVpdHelp(root = document) {
    const titles = root.querySelectorAll?.('.climate-chart-title h4') || [];
    for (const title of titles) {
      if (!/Atmospheric dryness\s*·\s*VPD/i.test(title.textContent || '')) continue;
      if (title.querySelector('.vpd-help-link')) continue;
      const link = document.createElement('a');
      link.className = 'vpd-help-link';
      link.href = 'methods.html#vpd-calculation';
      link.textContent = 'What’s VPD?';
      link.setAttribute('aria-label', 'What is VPD? Open the VPD calculation method');
      title.append(document.createTextNode(' '), link);
    }
  }

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      applyVpdHelp();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule, { once: true });
  } else {
    schedule();
  }
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
})();
