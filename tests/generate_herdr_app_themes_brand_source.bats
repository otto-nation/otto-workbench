#!/usr/bin/env bats
# generate-herdr-app-themes — where the brand comes from: tokens.json, the pin, install, cache, fetch.
bats_require_minimum_version 1.5.0
# select-on: bin/local/generate-herdr-app-themes ai/herdr/app/palettes/*.toml ai/herdr/app/themes/*.generated.json ai/herdr/app/mods/_lib/brand.generated.js site/package.json site/package-lock.json

setup() {
  load 'test_helper'
  load 'generate_herdr_app_themes_helper'
  common_setup
  GEN="$REPO_ROOT/bin/local/generate-herdr-app-themes"
  PAL="$BATS_TEST_TMPDIR/palettes"
  OUT="$BATS_TEST_TMPDIR/themes"
  mkdir -p "$PAL" "$OUT"
}

teardown() {
  common_teardown
}

@test "invalid tokens.json exits 3 and writes nothing" {
  sandbox_state_dir
  printf '{\n' > "$BATS_TEST_TMPDIR/tokens.json"
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 3 ]
  [[ "$output" == *"is not JSON"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "tokens.json schemaVersion 2 exits 3 and writes nothing" {
  sandbox_state_dir
  _tokens
  python3 - "$BATS_TEST_TMPDIR/tokens.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text())
data["schemaVersion"] = 2
p.write_text(json.dumps(data, indent=2) + "\n")
PY
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 3 ]
  [[ "$output" == *"schemaVersion 2"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "brand references with no --tokens exit 3 and write nothing" {
  sandbox_state_dir
  _brand_site
  _brand_palette
  run "$GEN" --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"brand tokens unavailable"* ]]
  [[ "$output" == *"bin/local/dev-deps"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "pinned node_modules tokens resolve without --tokens" {
  sandbox_state_dir
  _brand_site 1.2.0
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
}

@test "a node_modules brand at a different version than the pin is unused" {
  sandbox_state_dir
  _brand_site 9.9.9
  _brand_palette
  run "$GEN" --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"brand tokens unavailable"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "a lock resolved that differs from the package.json url is stale" {
  sandbox_state_dir
  _brand_site 1.2.0 "file://${BATS_TEST_TMPDIR}/other.tgz"
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"package-lock.json is stale"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "package.json with no brand dependency exits 3" {
  sandbox_state_dir
  _brand_site "" "" none
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"@otto-nation/brand"* ]]
}

@test "a file:// tarball renders and is cached under the state dir" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
  [ -f "$WORKBENCH_STATE_DIR/herdr-app-themes/3/brand.tgz" ]
}

@test "a second --offline run succeeds from cache after the tarball is gone" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  rm -f "$BATS_TEST_TMPDIR/brand.tgz" "$OUT"/*.generated.json
  run "$GEN" --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
}

@test "an integrity mismatch exits 3 and caches no file and no tmp" {
  sandbox_state_dir
  _brand_tarball
  python3 -c "import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text())
data['packages']['node_modules/@otto-nation/brand']['integrity'] = 'sha512-dGhlamNSrkUGCDEZAWE5w=='
p.write_text(json.dumps(data))" "$BATS_TEST_TMPDIR/site/package-lock.json"
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"integrity"* ]]
  [ ! -e "$WORKBENCH_STATE_DIR/herdr-app-themes" ]
}

@test "a corrupted cached tarball with --offline exits 3 and is not trusted" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  printf 'nope' > "$WORKBENCH_STATE_DIR/herdr-app-themes/3/brand.tgz"
  rm -f "$OUT"/*.generated.json
  run "$GEN" --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "--check with a valid file:// tarball and empty cache exits 3 and does not fetch" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 0 ]
  run "$GEN" --check --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"bin/local/dev-deps"* ]]
  [ ! -e "$WORKBENCH_STATE_DIR/herdr-app-themes/3/brand.tgz" ]
}

@test "--resolve-brand --offline exits 3 on an empty cache and 0 from cache after a fetch" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --resolve-brand --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [ ! -e "$WORKBENCH_STATE_DIR/herdr-app-themes/3/brand.tgz" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
  run "$GEN" --resolve-brand --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$output" = "fetched" ]
  [ -f "$WORKBENCH_STATE_DIR/herdr-app-themes/3/brand.tgz" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
  run "$GEN" --resolve-brand --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$output" = "cache" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "an unknown otto brand token or drifted otto JSON fails --check" {
  run "$GEN" --check
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  python3 - "$REPO_ROOT" <<'PY'
import sys, tomllib
from pathlib import Path
palette = tomllib.loads(
    (Path(sys.argv[1]) / "ai/herdr/app/palettes/otto.toml").read_text()
)["palette"]
for name, value in palette.items():
    if name.endswith("_derived"):
        continue
    want = "brand:ow-" + name.replace("_", "-")
    if value != want:
        raise SystemExit(f"{name}: {value!r} is not {want!r}")
PY
}

@test "an inherited GIT_DIR does not retarget the repo root" {
  # A git hook exports GIT_DIR, and git then skips discovery: the
  # `git -C <script dir> rev-parse --show-toplevel` this used to call answered
  # the cwd (bin/local) and looked for site/package.json next to the script.
  sandbox_state_dir
  mkdir -p "$BATS_TEST_TMPDIR/nogit"
  cd "$BATS_TEST_TMPDIR"
  GIT_DIR="$BATS_TEST_TMPDIR/nogit" run "$GEN" --resolve-brand --offline
  [[ "$output" != *"bin/local/site"* ]]
  # 0 (tokens found) or 3 (brand unavailable) both mean site/package.json was
  # read from the repo root; a git lookup under GIT_DIR never gets that far.
  [ "$status" -eq 0 ] || [ "$status" -eq 3 ]
}

@test "a tarball without package/tokens.json exits 3 naming ≥ 1.4.0" {
  sandbox_state_dir
  local root="$BATS_TEST_TMPDIR/pkg" tgz="$BATS_TEST_TMPDIR/brand.tgz" integrity
  mkdir -p "$root/package"
  printf 'x\n' > "$root/package/README"
  COPYFILE_DISABLE=1 tar -czf "$tgz" -C "$root" package
  integrity="$(python3 -c 'import base64, hashlib, pathlib, sys
print("sha512-" + base64.b64encode(hashlib.sha512(pathlib.Path(sys.argv[1]).read_bytes()).digest()).decode())' "$tgz")"
  mkdir -p "$BATS_TEST_TMPDIR/site"
  printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"file://${tgz}\"}}" > "$BATS_TEST_TMPDIR/site/package.json"
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"file://${tgz}\", \"integrity\": \"${integrity}\"}}}" > "$BATS_TEST_TMPDIR/site/package-lock.json"
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"package/tokens.json"* ]]
  [[ "$output" == *"1.4.0"* ]]
}
