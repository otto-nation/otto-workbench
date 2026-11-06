#!/usr/bin/env bats
# Tests for bin/local/generate-herdr-app-themes — palettes render to herdr-app theme files.
bats_require_minimum_version 1.5.0
# select-on: bin/local/generate-herdr-app-themes ai/herdr/app/palettes/*.toml ai/herdr/app/themes/*.json

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

@test "committed theme files match their palettes" {
  run "$GEN" --check
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "renders every palette name to its hex" {
  _palette paper
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 0 ]
  [ "$(jq -c . "$OUT/sample.json")" = '{"background":"#eeeeee","ansi":["#111111","#eeeeee"],"colors":{"accent":"#111111"}}' ]
}

@test "--check reports a stale theme file and leaves it" {
  _palette paper
  printf '%s\n' '{"background":"#000000"}' > "$OUT/sample.json"
  run "$GEN" --check --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"sample.json is stale"* ]]
  [ "$(cat "$OUT/sample.json")" = '{"background":"#000000"}' ]
}

@test "--check reports a missing theme file" {
  _palette paper
  run "$GEN" --check --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 1 ]
  [ ! -e "$OUT/sample.json" ]
}

@test "a value that is not a palette name fails without writing" {
  _palette "#eeeeee"
  run "$GEN" --palettes "$PAL" --out "$OUT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"themes.sample.background"*"not a name in [palette]"* ]]
  [ ! -e "$OUT/sample.json" ]
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
