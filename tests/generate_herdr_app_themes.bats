#!/usr/bin/env bats
# Tests for bin/local/generate-herdr-app-themes — palettes render to herdr-app theme files.
bats_require_minimum_version 1.5.0
# select-on: bin/local/generate-herdr-app-themes ai/herdr/app/palettes/*.toml ai/herdr/app/themes/*.generated.json

setup() {
  load 'test_helper'
  common_setup
  GEN="$REPO_ROOT/bin/local/generate-herdr-app-themes"
  PAL="$BATS_TEST_TMPDIR/palettes"
  OUT="$BATS_TEST_TMPDIR/themes"
  mkdir -p "$PAL" "$OUT"
}

teardown() {
  common_teardown
}

_palette() {
  cat > "$PAL/test.toml" <<EOF
[palette]
ink = "#111111"
paper = "#eeeeee"

[themes.sample]
background = "$1"
ansi = ["ink", "paper"]

[themes.sample.colors]
accent = "ink"
EOF
}

_tokens() {
  cat > "$BATS_TEST_TMPDIR/tokens.css" <<'EOF'
:root {
  /* } */
  --ow-canvas: #f7f2e9;
  --ow-only-root: #abcdef;
}
.dark {
  --ow-canvas: #0f0e13;
}
EOF
}

_brand_palette() {
  cat > "$PAL/test.toml" <<EOF
[palette]
canvas = "brand:ow-canvas"
only = "brand:${1:-ow-only-root}"

[schemes]
sample-light = "light"
sample-dark = "${2:-dark}"

[themes.sample-light]
background = "canvas"

[themes.sample-dark]
background = "canvas"
accent = "only"
EOF
}

_brand_site() {
  local site="$BATS_TEST_TMPDIR/site" url="file://${BATS_TEST_TMPDIR}/brand.tgz"
  mkdir -p "$site"
  if [[ "${3:-}" == none ]]; then
    printf '%s\n' '{}' > "$site/package.json"
  else
    printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"$url\"}}" > "$site/package.json"
  fi
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"${2:-$url}\", \"integrity\": \"sha512-fake\"}}}" > "$site/package-lock.json"
  [[ -z "${1:-}" ]] && return 0
  mkdir -p "$site/node_modules/@otto-nation/brand/src"
  printf '%s\n' "{\"version\": \"$1\"}" > "$site/node_modules/@otto-nation/brand/package.json"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.css" "$site/node_modules/@otto-nation/brand/src/tokens.css"
}

_brand_tarball() {
  local root="$BATS_TEST_TMPDIR/pkg" tgz="$BATS_TEST_TMPDIR/brand.tgz" integrity
  mkdir -p "$root/package/src"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.css" "$root/package/src/tokens.css"
  COPYFILE_DISABLE=1 tar -czf "$tgz" -C "$root" package
  integrity="$(python3 -c 'import base64, hashlib, pathlib, sys
print("sha512-" + base64.b64encode(hashlib.sha512(pathlib.Path(sys.argv[1]).read_bytes()).digest()).decode())' "$tgz")"
  mkdir -p "$BATS_TEST_TMPDIR/site"
  printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"file://${tgz}\"}}" > "$BATS_TEST_TMPDIR/site/package.json"
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"file://${tgz}\", \"integrity\": \"${integrity}\"}}}" > "$BATS_TEST_TMPDIR/site/package-lock.json"
}

@test "committed theme files match their palettes" {
  run "$GEN" --check
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "renders every palette name to its hex" {
  _palette paper
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 0 ]
  [ "$(jq -c . "$OUT/sample.generated.json")" = '{"background":"#eeeeee","ansi":["#111111","#eeeeee"],"colors":{"accent":"#111111"}}' ]
}

@test "--check reports a stale theme file and leaves it" {
  _palette paper
  printf '%s\n' '{"background":"#000000"}' > "$OUT/sample.generated.json"
  run "$GEN" --check --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"sample.generated.json is stale"* ]]
  [ "$(cat "$OUT/sample.generated.json")" = '{"background":"#000000"}' ]
}

@test "--check reports a missing theme file" {
  _palette paper
  run "$GEN" --check --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 1 ]
  [ ! -e "$OUT/sample.generated.json" ]
}

@test "a value that is not a palette name fails without writing" {
  _palette "#eeeeee"
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"themes.sample.background"*"not a name in [palette]"* ]]
  [ ! -e "$OUT/sample.generated.json" ]
}

@test "a palette entry that is not #rrggbb fails" {
  _palette paper
  sed -i.bak 's/^ink = .*/ink = "black"/' "$PAL/test.toml"
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"palette.ink"*"not #rrggbb"* ]]
}

@test "two palettes defining one theme fail" {
  _palette paper
  cp "$PAL/test.toml" "$PAL/other.toml"
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"defined by another palette"* ]]
}

@test "a failed write leaves no temp file behind" {
  _palette paper
  mkdir "$OUT/sample.generated.json"
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -ne 0 ]
  [ -z "$(find "$OUT" -name '*.tmp')" ]
}

@test "light and dark brand:ow-canvas resolve to each block's hex" {
  sandbox_state_dir
  _tokens
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
  [ "$(jq -r .background "$OUT/sample-dark.generated.json")" = "#0f0e13" ]
}

@test "a dark theme resolves a token only :root defines" {
  sandbox_state_dir
  _tokens
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 0 ]
  [ "$(jq -r .accent "$OUT/sample-dark.generated.json")" = "#abcdef" ]
}

@test "an unknown brand token exits 2 and writes nothing" {
  sandbox_state_dir
  _tokens
  _brand_palette ow-missing
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 2 ]
  [[ "$output" == *"palette.only"*"ow-missing"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "a brand reference with no [schemes] entry exits 2" {
  sandbox_state_dir
  _tokens
  _brand_palette
  sed -i.bak '/^\[schemes\]/,/^$/d' "$PAL/test.toml"
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no [schemes] entry"* ]]
}

@test "a scheme value other than light/dark exits 2" {
  sandbox_state_dir
  _tokens
  _brand_palette ow-only-root sepia
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 2 ]
  [[ "$output" == *"not light or dark"* ]]
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
  [ -f "$WORKBENCH_STATE_DIR/herdr-app-themes/brand.tgz" ]
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
  printf 'nope' > "$WORKBENCH_STATE_DIR/herdr-app-themes/brand.tgz"
  rm -f "$OUT"/*.generated.json
  run "$GEN" --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "--check with a valid file:// tarball and empty cache exits 3 and does not fetch" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.css"
  [ "$status" -eq 0 ]
  run "$GEN" --check --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [[ "$output" == *"bin/local/dev-deps"* ]]
  [ ! -e "$WORKBENCH_STATE_DIR/herdr-app-themes/brand.tgz" ]
}

@test "--resolve-brand --offline exits 3 on an empty cache and 0 from cache after a fetch" {
  sandbox_state_dir
  _brand_tarball
  _brand_palette
  run "$GEN" --resolve-brand --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 3 ]
  [ ! -e "$WORKBENCH_STATE_DIR/herdr-app-themes/brand.tgz" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
  run "$GEN" --resolve-brand --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$output" = "fetched" ]
  [ -f "$WORKBENCH_STATE_DIR/herdr-app-themes/brand.tgz" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
  run "$GEN" --resolve-brand --offline --palettes "$PAL" --out "$OUT" --site "$BATS_TEST_TMPDIR/site"
  [ "$status" -eq 0 ]
  [ "$output" = "cache" ]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "a tarball without package/src/tokens.css exits 3" {
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
  [[ "$output" == *"package/src/tokens.css"* ]]
}
