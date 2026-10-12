'use strict';

// Prevent browser locale changes (e.g. zh-CN/sv-SE) from changing the
// English-language site's visible numbers and dates.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const assetDir = path.join(__dirname, '..', 'assets');
const browserFiles = fs.readdirSync(assetDir).filter(name => name.endsWith('.js'));
let formatCallCount = 0;
for (const file of browserFiles) {
  const source = fs.readFileSync(path.join(assetDir, file), 'utf8');
  const calls = [
    ...source.matchAll(/\.toLocale(?:Date|Time)?String\s*\(\s*([^,)\n]*)/g),
    ...source.matchAll(/new Intl\.(?:NumberFormat|DateTimeFormat)\s*\(\s*([^,)\n]*)/g),
  ];
  for (const match of calls) {
    formatCallCount += 1;
    const locale = match[1].trim();
    assert.match(locale, /^(['"])en(?:-[A-Za-z0-9]+)*\1$/, `${file}: browser-dependent formatter: ${match[0]}`);
  }
}
assert.ok(formatCallCount >= 8, 'Expected to audit all existing UI locale formatters');

// Exercise the real wildfire formatter without the browser DOM, including the
// exact date shown in the reported screenshot.
const priorDetails = global.detailCells;
global.detailCells = () => '';
let formatWildfireDate;
try {
  ({ formatWildfireDate } = require('../assets/wildfire-source-sync-ui.js'));
} finally {
  if (priorDetails === undefined) delete global.detailCells;
  else global.detailCells = priorDetails;
}
assert.strictEqual(formatWildfireDate('2026-10-11'), 'Oct 11, 2026');
assert.strictEqual(formatWildfireDate('2026-10-11T21:17:00Z'), 'Oct 11, 2026');
assert.strictEqual(formatWildfireDate('2026-01-09'), 'Jan 9, 2026');
assert.strictEqual(formatWildfireDate(''), '–');
assert.strictEqual(formatWildfireDate('not-a-date'), 'not-a-date');

console.log(`English display locale tests: OK (${browserFiles.length} scripts, ${formatCallCount} formatters)`);
