'use strict';

const assert = require('assert');
const { parsePopulationToken, displayPopulation, roundText } = require('../assets/display-rounding-ui.js');

assert.strictEqual(parsePopulationToken('4,114'), 4114);
assert.strictEqual(parsePopulationToken('4 114'), 4114);
assert.strictEqual(parsePopulationToken('4\u00a0114'), 4114);
assert.strictEqual(parsePopulationToken('4\u202f114'), 4114);
assert.strictEqual(parsePopulationToken('4.114'), 4114);

assert.strictEqual(displayPopulation(4114), '≈4,000');
assert.strictEqual(displayPopulation(10999), '≈11,000');
assert.strictEqual(displayPopulation(13414), '≈13,000');
assert.strictEqual(displayPopulation(27615), '≈28,000');
assert.strictEqual(displayPopulation(169622), '≈170,000');
assert.strictEqual(displayPopulation(999), '<1,000');

// Reproduces the bug seen in locales that use spaces/NBSP for thousands.
assert.strictEqual(roundText('4\u00a0114 people'), '≈4,000 people');
assert.strictEqual(roundText('10\u202f999 people'), '≈11,000 people');
assert.strictEqual(roundText('13 414 people'), '≈13,000 people');
assert.strictEqual(roundText('27 615 people'), '≈28,000 people');
assert.strictEqual(roundText('169 622 people'), '≈170,000 people');
assert.strictEqual(roundText('<1,000 people'), '<1,000 people');
assert.strictEqual(roundText('≈4,000 people'), '≈4,000 people');

console.log('display rounding tests: OK');
