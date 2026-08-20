import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname } from 'node:path';
import test from 'node:test';

// app/icon.svg cannot be a package import: Next's metadata convention resolves
// app/icon.svg from disk, so this one asset is a copy rather than a dependency.
// A copy drifts; this is what keeps the favicon and the mark the same image.
test('app/icon.svg matches the brand mark byte for byte', () => {
  const require = createRequire(import.meta.url);
  // The package's exports map is closed and does not expose ./src/*, so the
  // mark is reached through package.json — the one entry that is exported —
  // rather than by a subpath specifier Node would refuse to resolve.
  const packageRoot = dirname(require.resolve('@otto-nation/brand/package.json'));
  const packaged = `${packageRoot}/src/marks/icon.svg`;
  assert.deepEqual(
    readFileSync(new URL('./app/icon.svg', import.meta.url)),
    readFileSync(packaged),
    `app/icon.svg has drifted from ${packaged} — copy the package version over it`,
  );
});
