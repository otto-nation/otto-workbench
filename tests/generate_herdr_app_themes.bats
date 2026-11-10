#!/usr/bin/env bats
# Tests for bin/local/generate-herdr-app-themes — palettes render to herdr-app theme files.
bats_require_minimum_version 1.5.0
# select-on: bin/local/generate-herdr-app-themes ai/herdr/app/palettes/*.toml ai/herdr/app/themes/*.generated.json ai/herdr/app/mods/_lib/brand.generated.js site/package.json site/package-lock.json

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
  cat > "$BATS_TEST_TMPDIR/tokens.json" <<'EOF'
{
  "schemaVersion": 1,
  "colors": {
    "light": {
      "canvas": "#f7f2e9",
      "only-root": "#abcdef"
    },
    "dark": {
      "canvas": "#0f0e13",
      "only-root": "#abcdef"
    }
  },
  "typography": {
    "meta": { "fontSize": "11px", "fontWeight": 400, "lineHeight": "16px" },
    "section": { "fontSize": "11px", "fontWeight": 600, "lineHeight": "16px" },
    "label": { "fontSize": "12px", "fontWeight": 500, "lineHeight": "16px" },
    "body": { "fontSize": "13px", "fontWeight": 400, "lineHeight": "18px" },
    "title": { "fontSize": "14px", "fontWeight": 600, "lineHeight": "20px" },
    "code": { "fontSize": "12px", "fontWeight": 400, "lineHeight": "16px" }
  },
  "lengths": {
    "control-height": "21px",
    "control-height-dense": "22px",
    "row-height": "33px",
    "row-height-dense": "34px",
    "control-padding": "15px",
    "card-padding": "16px",
    "column-min": "270px"
  },
  "radii": { "sm": "4px", "md": "6px", "lg": "8px" },
  "spacing": { "1": "4px", "2": "8px", "3": "12px", "4": "16px", "5": "20px", "6": "24px" }
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
  mkdir -p "$site/node_modules/@otto-nation/brand"
  printf '%s\n' "{\"version\": \"$1\"}" > "$site/node_modules/@otto-nation/brand/package.json"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.json" "$site/node_modules/@otto-nation/brand/tokens.json"
}

_brand_tarball() {
  local root="$BATS_TEST_TMPDIR/pkg" tgz="$BATS_TEST_TMPDIR/brand.tgz" integrity
  mkdir -p "$root/package"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.json" "$root/package/tokens.json"
  COPYFILE_DISABLE=1 tar -czf "$tgz" -C "$root" package
  integrity="$(python3 -c 'import base64, hashlib, pathlib, sys
print("sha512-" + base64.b64encode(hashlib.sha512(pathlib.Path(sys.argv[1]).read_bytes()).digest()).decode())' "$tgz")"
  mkdir -p "$BATS_TEST_TMPDIR/site"
  printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"file://${tgz}\"}}" > "$BATS_TEST_TMPDIR/site/package.json"
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"file://${tgz}\", \"integrity\": \"${integrity}\"}}}" > "$BATS_TEST_TMPDIR/site/package-lock.json"
}

_terminal_dir() {
  local dir="$BATS_TEST_TMPDIR/terminal"
  mkdir -p "$dir"
  python3 - "$dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])

def theme(bg, fg):
    return {
        "background": bg,
        "foreground": fg,
        "selection": "#222222",
        "cursor": "#333333",
        "ansi": [f"#{i:02x}{i:02x}{i:02x}" for i in range(16)],
    }

(root / "otto-light.json").write_text(json.dumps(theme("#aaaaaa", "#111111"), indent=2) + "\n")
(root / "otto-dark.json").write_text(json.dumps(theme("#010101", "#fefefe"), indent=2) + "\n")
PY
}

_terminal_palette() {
  cat > "$PAL/test.toml" <<'EOF'
[palette]
canvas = "brand:ow-canvas"

[schemes]
sample-light = "light"
sample-dark = "dark"

[themes.sample-light]
terminal = "brand"

[themes.sample-light.colors]
accent = "canvas"

[themes.sample-dark]
terminal = "brand"

[themes.sample-dark.colors]
accent = "canvas"
EOF
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

@test "light and dark brand:ow-canvas resolve to each scheme's hex" {
  sandbox_state_dir
  _tokens
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
  [ "$(jq -r .background "$OUT/sample-dark.generated.json")" = "#0f0e13" ]
}

@test "a dark theme does not inherit a token only colors.light defines" {
  sandbox_state_dir
  _tokens
  python3 - "$BATS_TEST_TMPDIR/tokens.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text())
del data["colors"]["dark"]["only-root"]
p.write_text(json.dumps(data, indent=2) + "\n")
PY
  _brand_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 2 ]
  [[ "$output" == *"palette.only"*"ow-only-root"*"not a token"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "an unknown brand token exits 2 and writes nothing" {
  sandbox_state_dir
  _tokens
  _brand_palette ow-missing
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 2 ]
  [[ "$output" == *"palette.only"*"ow-missing"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "a brand reference with no [schemes] entry exits 2" {
  sandbox_state_dir
  _tokens
  _brand_palette
  sed -i.bak '/^\[schemes\]/,/^$/d' "$PAL/test.toml"
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no [schemes] entry"* ]]
}

@test "a scheme value other than light/dark exits 2" {
  sandbox_state_dir
  _tokens
  _brand_palette ow-only-root sepia
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 2 ]
  [[ "$output" == *"not light or dark"* ]]
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

@test "terminal = brand fills light and dark from --terminal DIR" {
  sandbox_state_dir
  _tokens
  _terminal_dir
  _terminal_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json" --terminal "$BATS_TEST_TMPDIR/terminal"
  [ "$status" -eq 0 ]
  [ "$(jq -r .background "$OUT/sample-light.generated.json")" = "#aaaaaa" ]
  [ "$(jq -r .foreground "$OUT/sample-light.generated.json")" = "#111111" ]
  [ "$(jq -r .selection "$OUT/sample-light.generated.json")" = "#222222" ]
  [ "$(jq -r .cursor "$OUT/sample-light.generated.json")" = "#333333" ]
  [ "$(jq -r .colors.accent "$OUT/sample-light.generated.json")" = "#f7f2e9" ]
  [ "$(jq -r .background "$OUT/sample-dark.generated.json")" = "#010101" ]
  [ "$(jq -r .foreground "$OUT/sample-dark.generated.json")" = "#fefefe" ]
  [ "$(jq -r '.ansi | length' "$OUT/sample-light.generated.json")" = "16" ]
  [ "$(jq -r .ansi[0] "$OUT/sample-light.generated.json")" = "#000000" ]
}

@test "terminal = brand with a filled key exits 2 without writing" {
  sandbox_state_dir
  _tokens
  _terminal_dir
  _terminal_palette
  python3 - "$PAL/test.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
needle = '[themes.sample-light]\nterminal = "brand"'
insert = needle + '\nbackground = "canvas"'
text = p.read_text()
if needle not in text:
    raise SystemExit('theme table not found')
p.write_text(text.replace(needle, insert, 1))
PY
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json" --terminal "$BATS_TEST_TMPDIR/terminal"
  [ "$status" -eq 2 ]
  [[ "$output" == *"terminal = \"brand\" cannot set background"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "terminal = brand without terminal files exits 2 without writing" {
  sandbox_state_dir
  _tokens
  _terminal_palette
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 2 ]
  [[ "$output" == *"needs @otto-nation/brand ≥ 1.3.0"* ]]
  [ -z "$(find "$OUT" -name '*.generated.json')" ]
}

@test "renders brand sizes and type roles from --tokens" {
  sandbox_state_dir
  _tokens
  _palette paper
  run "$GEN" --palettes "$PAL" --out "$OUT" --mods-lib "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 0 ]
  [ "$(cat "$OUT/brand.generated.js")" = "$(cat <<'EOF'
// Generated by bin/local/generate-herdr-app-themes from @otto-nation/brand tokens.json; do not edit.
export const BRAND = {
  "controlHeight": 21,
  "controlHeightDense": 22,
  "rowHeight": 33,
  "rowHeightDense": 34,
  "controlPadding": 15,
  "cardPadding": 16,
  "columnMin": 270,
  "font": {
    "meta": {
      "size": 11,
      "weight": 400,
      "lineHeight": 16
    },
    "section": {
      "size": 11,
      "weight": 600,
      "lineHeight": 16
    },
    "label": {
      "size": 12,
      "weight": 500,
      "lineHeight": 16
    },
    "body": {
      "size": 13,
      "weight": 400,
      "lineHeight": 18
    },
    "title": {
      "size": 14,
      "weight": 600,
      "lineHeight": 20
    },
    "code": {
      "size": 12,
      "weight": 400,
      "lineHeight": 16
    }
  },
  "radius": {
    "sm": 4,
    "md": 6,
    "lg": 8
  },
  "space": {
    "1": 4,
    "2": 8,
    "3": 12,
    "4": 16,
    "5": 20,
    "6": 24
  }
};
EOF
)" ]
}

@test "--check reports a stale brand module and leaves it" {
  sandbox_state_dir
  _tokens
  _palette paper
  run "$GEN" --palettes "$PAL" --out "$OUT" --mods-lib "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 0 ]
  printf '%s\n' '// edited' > "$OUT/brand.generated.js"
  run "$GEN" --check --palettes "$PAL" --out "$OUT" --mods-lib "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 1 ]
  [[ "$output" == *"brand.generated.js is stale"* ]]
  [ "$(cat "$OUT/brand.generated.js")" = "// edited" ]
}

@test "a --palettes run without --mods-lib writes no brand module" {
  sandbox_state_dir
  _tokens
  _palette paper
  run "$GEN" --palettes "$PAL" --out "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 0 ]
  [ ! -e "$OUT/brand.generated.js" ]
}

@test "a missing length exits 3 without writing the brand module" {
  sandbox_state_dir
  _tokens
  _palette paper
  python3 - "$BATS_TEST_TMPDIR/tokens.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text())
del data["lengths"]["control-height"]
p.write_text(json.dumps(data, indent=2) + "\n")
PY
  run "$GEN" --palettes "$PAL" --out "$OUT" --mods-lib "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 3 ]
  [[ "$output" == *"lengths.control-height"* ]]
  [ ! -e "$OUT/brand.generated.js" ]
}

@test "a non-px length is refused" {
  sandbox_state_dir
  _tokens
  _palette paper
  python3 - "$BATS_TEST_TMPDIR/tokens.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text())
data["typography"]["section"]["lineHeight"] = "1.3"
p.write_text(json.dumps(data, indent=2) + "\n")
PY
  run "$GEN" --palettes "$PAL" --out "$OUT" --mods-lib "$OUT" --tokens "$BATS_TEST_TMPDIR/tokens.json"
  [ "$status" -eq 3 ]
  [[ "$output" == *"typography.section.lineHeight"*"is not a px length"* ]]
  [ ! -e "$OUT/brand.generated.js" ]
}

@test "otto.toml carries no literal hex: every colour comes from the brand" {
  local palette="$REPO_ROOT/ai/herdr/app/palettes/otto.toml"
  [ -f "$palette" ]
  run grep -nE '"#[0-9a-fA-F]{3,8}"' "$palette"
  [ "$status" -eq 1 ]
  grep -qx 'terminal = "brand"' "$palette"
}
