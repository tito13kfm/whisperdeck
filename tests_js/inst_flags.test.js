// Regression guard for issue #229: dashboard VU and transcribe scope flags
// must be independent, and must reset on teardown so a round-trip
// dashboard -> transcribe -> dashboard does not leave the VU meter dead.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const rack = fs.readFileSync(path.join(__dirname, '..', 'static', 'rack.js'), 'utf8');

test('INST declares scopeBgInit and dashVuInit, not scopeInit', () => {
  // One shared scopeInit flag was the bug; it should not exist on INST anymore.
  assert.match(rack, /scopeBgInit:\s*false/);
  assert.match(rack, /dashVuInit:\s*false/);
  // No remaining INST.scopeInit anywhere (shared flag removed)
  const hits = [...rack.matchAll(/INST\.scopeInit/g)];
  assert.equal(hits.length, 0, `expected 0 INST.scopeInit references, found ${hits.length}`);
});

test('drawScope guards on scopeBgInit, not dashVuInit', () => {
  // drawScope background fill must use the transcribe-scoped flag
  assert.match(rack, /if\s*\(!INST\.scopeBgInit\)\s*\{/);
  assert.match(rack, /INST\.scopeBgInit\s*=\s*true/);
  // drawScope should not touch dashVuInit
  const scopeSection = rack.slice(rack.indexOf('function drawScope'));
  const dashVuInScope = (scopeSection.match(/dashVuInit/g) || []).length;
  // drawScope itself must not reference dashVuInit (teardown elsewhere may)
  // Extract just the drawScope body (ends before function startInstruments)
  const body = scopeSection.slice(0, scopeSection.indexOf('function startInstruments'));
  assert.equal((body.match(/dashVuInit/g) || []).length, 0);
  void dashVuInScope;
});

test('dashInitVu guards and sets dashVuInit', () => {
  assert.match(rack, /if\s*\(!INST\.dashVuInit\)\s*dashInitVu\(\)/);
  assert.match(rack, /function dashInitVu\(\)\s*\{\s*if\s*\(INST\.dashVuInit\)\s*return;/);
  assert.match(rack, /INST\.dashVuInit\s*=\s*true/);
});

test('each instrument loop resets its own flag on teardown', () => {
  // dashboard frame leaving the page resets dashVuInit, transcribe loop resets scopeBgInit
  assert.match(rack, /S\.page !== 'dashboard'.*INST\.dashVuInit\s*=\s*false/);
  assert.match(rack, /S\.page !== 'transcribe'.*INST\.scopeBgInit\s*=\s*false/);
  // No cross-reset (transcribe must not clear dashVuInit, dashboard must not clear scopeBgInit)
  const dashTeardown = rack.match(/S\.page !== 'dashboard'[^;]*;/)[0];
  assert.equal(/scopeBgInit/.test(dashTeardown), false, 'dashboard teardown must not touch scopeBgInit');
  const txTeardown = rack.match(/S\.page !== 'transcribe'[^;]*;/)[0];
  assert.equal(/dashVuInit/.test(txTeardown), false, 'transcribe teardown must not touch dashVuInit');
});

test('rack.min.js reflects the split flags (built artifact is fresh)', () => {
  const min = fs.readFileSync(path.join(__dirname, '..', 'static', 'rack.min.js'), 'utf8');
  assert.equal((min.match(/scopeInit/g) || []).length, 0, 'minified bundle should not contain legacy scopeInit');
  assert.ok(min.includes('scopeBgInit'), 'minified bundle should contain scopeBgInit');
  assert.ok(min.includes('dashVuInit'), 'minified bundle should contain dashVuInit');
});
