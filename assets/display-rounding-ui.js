'use strict';

// Display-only population rounding. Raw JSON values remain unchanged.
// The site is English, so display grouping is intentionally fixed to en-US.
(() => {
  const integerFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

  function parsePopulationToken(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    const normalized = String(value ?? '')
      .trim()
      // Browsers may render locale grouping as comma, period, normal space,
      // NBSP or narrow NBSP. Population values are integer counts, so these
      // are grouping separators here rather than decimal punctuation.
      .replace(/[,.\s\u00A0\u202F]/g, '');
    if (!/^\d+$/.test(normalized)) return null;
    const n = Number(normalized);
    return Number.isFinite(n) ? n : null;
  }

  function displayPopulation(value) {
    const n = parsePopulationToken(value);
    if (n === null) return null;
    if (n <= 0) return '0';
    if (n < 1000) return '<1,000';
    const rounded = Math.round(n / 1000) * 1000;
    return `≈${integerFormatter.format(rounded)}`;
  }

  // A grouped integer may contain locale-specific spaces. Only consume a
  // whitespace separator when it is followed by another digit, so the final
  // whitespace before “people” remains available to the suffix capture.
  const groupedInteger = String.raw`\d(?:[\d,.]|\s(?=\d))*`;
  const patterns = [
    new RegExp(`([<≈~])?(${groupedInteger})(\\s+people\\b)`, 'gi'),
    new RegExp(`([<≈~])?(${groupedInteger})(\\s+exposed in burned area\\b)`, 'gi'),
    new RegExp(`([<≈~])?(${groupedInteger})(\\s+within\\s+(?:1|2|5|10)\\s*km\\b)`, 'gi'),
  ];

  function roundText(text) {
    let out = String(text ?? '');
    for (const re of patterns) {
      out = out.replace(re, (_, prefix, number, suffix) => {
        if (prefix === '<') return `<${number}${suffix}`;
        const shown = displayPopulation(number);
        return shown === null ? `${prefix || ''}${number}${suffix}` : `${shown}${suffix}`;
      });
    }
    return out;
  }

  const api = { parsePopulationToken, displayPopulation, roundText };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

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
