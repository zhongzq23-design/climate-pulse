'use strict';

// Display-only population rounding. Raw JSON values remain unchanged.
(() => {
  function displayPopulation(value) {
    const n = Number(String(value).replace(/,/g, ''));
    if (!Number.isFinite(n)) return null;
    if (n <= 0) return '0';
    if (n < 1000) return '<1,000';
    const rounded = Math.round(n / 1000) * 1000;
    return `≈${rounded.toLocaleString()}`;
  }

  const patterns = [
    /([<≈~])?(\d[\d,]*)(\s+people\b)/gi,
    /([<≈~])?(\d[\d,]*)(\s+exposed in burned area\b)/gi,
    /([<≈~])?(\d[\d,]*)(\s+within\s+(?:1|2|5|10)\s*km\b)/gi,
  ];

  function roundText(text) {
    let out = text;
    for (const re of patterns) {
      out = out.replace(re, (_, prefix, number, suffix) => {
        if (prefix === '<') return `<${number}${suffix}`;
        const shown = displayPopulation(number);
        return shown === null ? `${prefix || ''}${number}${suffix}` : `${shown}${suffix}`;
      });
    }
    return out;
  }

  function apply(root = document.body) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || /^(SCRIPT|STYLE|NOSCRIPT)$/.test(parent.tagName)) continue;
      const next = roundText(node.nodeValue || '');
      if (next !== node.nodeValue) node.nodeValue = next;
    }
  }

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; apply(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
