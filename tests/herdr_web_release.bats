#!/usr/bin/env bats
# Tests for ai/herdr/web/bin/pr-board-release — the release tree contract (spec § 8.1).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js ai/herdr/app/themes/*

setup() {
  load 'test_helper'
  common_setup
  REL="$REPO_ROOT/ai/herdr/web/bin/pr-board-release"
  OUT="$BATS_TEST_TMPDIR/rel"
}
teardown() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  common_teardown
}

_sha_check() {
  if command -v sha256sum >/dev/null 2>&1; then (cd "$OUT" && sha256sum -c --quiet MANIFEST.sha256); else (cd "$OUT" && shasum -a 256 -c --quiet MANIFEST.sha256); fi
}

@test "release: builds the exact layout with VERSION and a verifying manifest" {
  run "$REL" --out "$OUT" --ref v9.9.9-test
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT/VERSION")" = "v9.9.9-test" ]
  for f in ai/herdr/web/server/main.js ai/herdr/web/package.json ai/herdr/web/.bun-version ai/herdr/web/lib/css.js \
           ai/herdr/web/public/index.html ai/herdr/app/mods/_lib/board_data.js ai/herdr/app/mods/_lib/brand.generated.js \
           ai/herdr/app/mods/_lib/board_store.js ai/herdr/app/mods/_lib/board_model.js ai/herdr/app/mods/_lib/board_pull.js ai/herdr/app/mods/_lib/board_doc.js \
           ai/herdr/app/themes/otto-light.generated.json ai/herdr/app/themes/otto-dark.generated.json; do
    [ -f "$OUT/$f" ]
  done
  [ ! -e "$OUT/ai/herdr/app/mods/prs" ]
  [ ! -e "$OUT/ai/herdr/app/mods/_ui" ]
  [ ! -e "$OUT/ai/herdr/app/themes/gruvbox-dark.generated.json" ]
  [ ! -e "$OUT/ai/herdr/web/bin" ]
  [ -z "$(find "$OUT" -name node_modules -o -name .git)" ]
  run _sha_check
  [ "$status" -eq 0 ]
  run grep -c . "$OUT/MANIFEST.sha256"
  [ "$output" -eq "$(find "$OUT" -type f ! -name MANIFEST.sha256 | wc -l | tr -d ' ')" ]
}

@test "release: refuses a non-empty out dir and a bad or missing ref (exit 2)" {
  mkdir -p "$OUT" && touch "$OUT/x"
  run "$REL" --out "$OUT" --ref v1
  [ "$status" -eq 2 ]
  run "$REL" --out "$BATS_TEST_TMPDIR/other" --ref 'v1;rm'
  [ "$status" -eq 2 ]
  run "$REL" --out "$BATS_TEST_TMPDIR/other"
  [ "$status" -eq 2 ]
  run "$REL" --ref v1
  [ "$status" -eq 2 ]
}

@test "release: the tree runs from anywhere and reports its VERSION" {
  local tok='0123456789abcdef0123456789abcdef'
  "$REL" --out "$OUT" --ref v9.9.9-test
  mkdir -p "$BATS_TEST_TMPDIR/stubs"
  printf '#!/usr/bin/env bash\necho %s\n' "'{\"terminals\":[]}'" > "$BATS_TEST_TMPDIR/stubs/hc"
  chmod +x "$BATS_TEST_TMPDIR/stubs/hc"
  chmod -R a-w "$OUT"
  # exec makes the backgrounded subshell become the server, so $! is the pid
  # teardown must kill. Backgrounding `cd / && env ...` instead makes $! a
  # wrapper shell, and killing it orphans the server.
  (cd / && exec env -i HOME="$BATS_TEST_TMPDIR" PATH="$BATS_TEST_TMPDIR/stubs:/usr/bin:/bin" PR_BOARD_PORT=0 \
     PR_BOARD_TOKEN="$tok" "$(command -v bun)" "$OUT/ai/herdr/web/server/main.js" \
     > "$BATS_TEST_TMPDIR/s.log" 2>&1 3>&-) &
  SERVER_PID=$!
  local port=""
  for _ in $(seq 1 50); do
    port="$(sed -n 's/.*listening on http:\/\/127\.0\.0\.1:\([0-9]*\).*/\1/p' "$BATS_TEST_TMPDIR/s.log")"
    [[ -n "$port" ]] && break
    sleep 0.1
  done
  run curl -fsS "http://127.0.0.1:$port/healthz"
  chmod -R u+w "$OUT"
  [ "$output" = '{"ok":true,"version":"v9.9.9-test"}' ]
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/"
  [ "$output" = "401" ]
  run curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" \
    "http://127.0.0.1:$port/"
  [ "$output" = "200" ]
  # SIGTERM to the recorded pid must stop the server itself, or teardown leaks it.
  kill -TERM "$SERVER_PID"
  local rc=0
  wait "$SERVER_PID" || rc=$?
  SERVER_PID=
  [ "$rc" -eq 0 ]
  run curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$port/healthz"
  [ "$status" -ne 0 ]
}

# Follows static `from`, side-effect `import './x'`, `export * from`, and
# dynamic `import('./x')` with a string-literal specifier. Specifiers that are
# not relative (or not a string) are ignored.
_walk_imports() {
  local root="$1"
  shift
  cat > "$BATS_TEST_TMPDIR/walk_imports.mjs" <<'JS'
import fs from "node:fs"; import path from "node:path";
const root = process.argv[2];
const entries = process.argv.slice(3);
const seen = new Set(); const missing = [];
const specsOf = (src) => {
  const specs = [];
  for (const m of src.matchAll(/(?:^|\n)\s*import\s*["'](\.{1,2}\/[^"']+)["']/g)) specs.push(m[1]);
  for (const m of src.matchAll(/(?:^|\n)\s*(?:import|export)\s[^;]*?\bfrom\s*["'](\.{1,2}\/[^"']+)["']/g)) specs.push(m[1]);
  for (const m of src.matchAll(/\bimport\s*\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/g)) specs.push(m[1]);
  return specs;
};
const walk = (f) => {
  if (seen.has(f)) return; seen.add(f);
  if (!fs.existsSync(f)) { missing.push(path.relative(root, f)); return; }
  for (const s of specsOf(fs.readFileSync(f, "utf8"))) walk(path.resolve(path.dirname(f), s));
};
for (const e of entries) walk(path.join(root, e));
process.stdout.write(JSON.stringify({
  seen: [...seen].map((f) => path.relative(root, f)).sort(),
  missing: missing.sort(),
}));
JS
  run node "$BATS_TEST_TMPDIR/walk_imports.mjs" "$root" "$@"
}

@test "release: every module the server and the page import is in the tree" {
  run "$REL" --out "$OUT" --ref v9.9.9-test
  [ "$status" -eq 0 ]
  _walk_imports "$OUT" ai/herdr/web/server/main.js ai/herdr/web/public/app.js
  [ "$status" -eq 0 ]
  missing="$(node -e 'process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).missing))' -- "$output")"
  [ "$missing" = '[]' ]
}

@test "release: the import walker follows from, side-effect, export-star, and dynamic import" {
  local tree="$BATS_TEST_TMPDIR/imports"
  mkdir -p "$tree"
  cat > "$tree/entry.js" <<'JS'
import { x } from './from.js';
import './side.js';
export * from './star.js';
const d = await import('./dyn.js');
JS
  _walk_imports "$tree" entry.js
  [ "$status" -eq 0 ]
  [ "$output" = '{"seen":["dyn.js","entry.js","from.js","side.js","star.js"],"missing":["dyn.js","from.js","side.js","star.js"]}' ]
}
