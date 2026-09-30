const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Regression (0.4.23 b8): the 0.4.9 version gate REJECTS every dispatch when
// the content script's VERSION differs from a provider's `version` field
// ("Extension files out of sync: content script X, provider Y"). 0.4.23
// bumped content.js/manifest.json to 0.4.23 but left all eight providers at
// "0.4.22", so the first real dedicated-browser run showed "版本不同步" and
// every task failed at the gate. This test loads the real files and asserts
// the versions agree, so the mismatch can never ship silently again.
const ext = path.join(__dirname, '..', 'extension');

test('provider versions match the content script VERSION (version gate would not fire)', () => {
  const content = fs.readFileSync(path.join(ext, 'content.js'), 'utf8');
  const m = content.match(/const VERSION = '([^']+)'/);
  assert.ok(m, 'content.js: VERSION constant not found');
  const version = m[1];

  const manifest = JSON.parse(fs.readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
  assert.equal(manifest.version, version,
    `manifest.json is ${manifest.version} but content script is ${version}`);

  const dir = path.join(ext, 'providers');
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.js'));
  assert.ok(files.length >= 8, `expected >=8 provider files, found ${files.length}`);
  let withVersion = 0;
  for (const f of files) {
    const v = fs.readFileSync(path.join(dir, f), 'utf8').match(/version:\s*"([^"]+)"/);
    if (!v) continue; // helper files (chatgpt-cm.js, qwen-net.js) carry no version
    withVersion++;
    assert.equal(v[1], version,
      `providers/${f} reports ${v[1]} but content script is ${version} -> ` +
      `every dispatch from ${f} would be rejected by the version gate`);
  }
  assert.ok(withVersion >= 8, `expected >=8 versioned providers, found ${withVersion}`);
});
