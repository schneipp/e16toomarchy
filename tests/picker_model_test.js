const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const model = {};
vm.createContext(model);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../omarchy-plugin/Model.js'), 'utf8').replace('.pragma library', ''), model);
const markup = '<img src="https://example.invalid/tracker">';
assert.equal(model.parseCatalog(JSON.stringify({themes: ['Aqua', markup]})).themes.length, 1);
assert.equal(model.parseState(JSON.stringify({stage: 'failed', error: markup})).error, markup); // Plain-text display contract.
assert.equal(model.previewUrl('https://example.invalid/tracker', '/cache'), '');
assert.equal(model.previewUrl('/cache/themes/e16-a/../../secret/e16/frame-preview.png', '/cache'), '');
assert.equal(model.previewUrl('/cache/themes/e16-a/e16/frame-preview.png', '/cache'), 'file:///cache/themes/e16-a/e16/frame-preview.png');
assert.equal(model.parseCatalog('x'.repeat(524289)).themes.length, 0);
assert.equal(model.parseState(JSON.stringify({busy: 'true', stage: markup})).busy, false);
assert.equal(model.parseState(JSON.stringify({busy: true, stage: markup})).stage, 'idle');
console.log('Picker model bounds, markup handling, and local preview URL tests passed');
