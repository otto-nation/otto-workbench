#!/usr/bin/env bats
# Tests for bin/local/generate-herdr-app-themes — palettes render to herdr-app theme files.
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

@test "otto.toml carries no literal hex: every colour comes from the brand" {
  local palette="$REPO_ROOT/ai/herdr/app/palettes/otto.toml"
  [ -f "$palette" ]
  run grep -nE '"#[0-9a-fA-F]{3,8}"' "$palette"
  [ "$status" -eq 1 ]
  grep -qx 'terminal = "brand"' "$palette"
}
