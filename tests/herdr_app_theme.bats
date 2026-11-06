#!/usr/bin/env bats
# Tests for step_herdr_app_theme — Gruvbox theme files and settings.json's theme key.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/steps.sh bin/local/generate-herdr-app-themes ai/herdr/app/themes/*.generated.json ai/herdr/app/palettes/*.toml lib/files.sh lib/config.sh lib/constants.sh

setup() {
  load 'test_helper'
  common_setup
  . "$REPO_ROOT/lib/portable.sh"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  APP="$HOME/.config/herdr-app"
  SRC="$REPO_ROOT/ai/herdr/app/themes"
  APP_LIGHT=$(sed -n 's/^HERDR_APP_THEME_LIGHT="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  APP_DARK=$(sed -n 's/^HERDR_APP_THEME_DARK="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  DESIRED_THEME=$(jq -cn --arg l "$APP_LIGHT" --arg d "$APP_DARK" '{light: $l, dark: $d}')
  unset HERDR_ENV HERDR_PANE_ID
  narrow_path_to bash jq
  BIN="$BATS_TEST_TMPDIR/narrow-bin"
}

teardown() {
  common_teardown
}

_stub_app() {
  printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/herdr-app"
  chmod +x "$BIN/herdr-app"
}

_run() {
  bash -c '
    set -e
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    export WORKBENCH_STABLE_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    "$2"
  ' _ "$REPO_ROOT" "$1"
}

@test "absent herdr-app is a no-op" {
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$APP" ]
}

@test "fresh install with binary only links themes and writes settings" {
  _stub_app
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -L "$APP/themes/${APP_DARK}.json" ]
  [ -L "$APP/themes/${APP_LIGHT}.json" ]
  [ "$(readlink "$APP/themes/${APP_DARK}.json")" = "$SRC/${APP_DARK}.generated.json" ]
  [ "$(readlink "$APP/themes/${APP_LIGHT}.json")" = "$SRC/${APP_LIGHT}.generated.json" ]
  [ "$(jq -S -c . "$APP/settings.json")" = "$(jq -S -c '{theme: .}' <<< "$DESIRED_THEME")" ]
  [ "$(file_mode "$APP/settings.json")" = "644" ]
}

@test "config dir only counts as installed" {
  mkdir -p "$APP"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -L "$APP/themes/${APP_DARK}.json" ]
  [ -f "$APP/settings.json" ]
}

@test "preserves other settings keys and does not add appearance" {
  _stub_app
  mkdir -p "$APP"
  seed='{"zoom":1.2,"sounds":{"enabled":true},"font_ligatures":false,"minimum_contrast":1,"theme":"paper"}'
  printf '%s\n' "$seed" > "$APP/settings.json"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ "$(jq -c 'del(.theme)' "$APP/settings.json")" = "$(jq -c 'del(.theme)' <<< "$seed")" ]
  [ "$(jq -c '.theme' "$APP/settings.json")" = "$DESIRED_THEME" ]
  [ "$(jq -r 'has("appearance")' "$APP/settings.json")" = "false" ]
}

@test "identical regular theme file is replaced by a symlink" {
  _stub_app
  mkdir -p "$APP/themes"
  cp "$SRC/${APP_DARK}.generated.json" "$APP/themes/${APP_DARK}.json"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -L "$APP/themes/${APP_DARK}.json" ]
  [[ "$output" != *"differs from the workbench copy"* ]]
}

@test "differing regular theme file is left with a warning" {
  _stub_app
  mkdir -p "$APP/themes"
  printf '%s\n' '{"background":"#000000"}' > "$APP/themes/${APP_DARK}.json"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ ! -L "$APP/themes/${APP_DARK}.json" ]
  [ "$(cat "$APP/themes/${APP_DARK}.json")" = '{"background":"#000000"}' ]
  [[ "$output" == *"differs from the workbench copy"* ]]
  [ -L "$APP/themes/${APP_LIGHT}.json" ]
  [ "$(jq -c '.theme' "$APP/settings.json")" = "$DESIRED_THEME" ]
}

@test "re-run under WORKBENCH_SYNC prints nothing and does not rewrite settings" {
  _stub_app
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  touch -t 202001010000 "$APP/settings.json"
  before=$(file_mtime "$APP/settings.json")
  before_bytes=$(cat "$APP/settings.json")
  dark_link=$(readlink "$APP/themes/${APP_DARK}.json")
  WORKBENCH_SYNC=true run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ "$(cat "$APP/settings.json")" = "$before_bytes" ]
  [ "$(file_mtime "$APP/settings.json")" = "$before" ]
  [ "$(readlink "$APP/themes/${APP_DARK}.json")" = "$dark_link" ]
}

@test "invalid settings.json is not clobbered" {
  _stub_app
  mkdir -p "$APP"
  local raw
  for raw in '{not json' '' '[]' '{}{}'; do
    printf '%s' "$raw" > "$APP/settings.json"
    run _run step_herdr_app_theme
    [ "$status" -eq 0 ]
    [ "$(cat "$APP/settings.json")" = "$raw" ]
    [[ "$output" == *"not a JSON object"* ]]
    [ -L "$APP/themes/${APP_DARK}.json" ]
  done
}

@test "symlinked settings.json is left alone" {
  _stub_app
  mkdir -p "$APP" "$TMPDIR/dotfiles"
  printf '%s\n' '{"theme":"paper"}' > "$TMPDIR/dotfiles/settings.json"
  ln -s "$TMPDIR/dotfiles/settings.json" "$APP/settings.json"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ -L "$APP/settings.json" ]
  [ "$(cat "$TMPDIR/dotfiles/settings.json")" = '{"theme":"paper"}' ]
  [[ "$output" == *"symlink"* ]]
}

@test "stale symlink is repointed at the workbench copy" {
  _stub_app
  mkdir -p "$APP/themes"
  ln -s /tmp/stale-theme.json "$APP/themes/${APP_DARK}.json"
  run _run step_herdr_app_theme
  [ "$status" -eq 0 ]
  [ "$(readlink "$APP/themes/${APP_DARK}.json")" = "$SRC/${APP_DARK}.generated.json" ]
}

@test "settings names resolve to shipped files" {
  [ -f "$SRC/${APP_LIGHT}.generated.json" ]
  [ -f "$SRC/${APP_DARK}.generated.json" ]
}

@test "shipped themes satisfy the app schema" {
  local file
  for file in "$SRC/${APP_LIGHT}.generated.json" "$SRC/${APP_DARK}.generated.json"; do
    jq -e '
      (.background | test("^#[0-9a-fA-F]{6}$")) and
      (.foreground | test("^#[0-9a-fA-F]{6}$")) and
      (.selection | test("^#[0-9a-fA-F]{6}$")) and
      ((.cursor // "#000000") | test("^#[0-9a-fA-F]{6}$")) and
      (.ansi | length == 16) and
      (.ansi | map(test("^#[0-9a-fA-F]{6}$")) | all)
    ' "$file" > /dev/null
  done
}

# herdr-app derives panel colors from the terminal palette by desaturating them, so a theme
# that names only `accent` gets grey chrome around Gruvbox terminals. Every panel color
# theme() exposes must be set explicitly.
@test "shipped themes set every panel color" {
  local file
  for file in "$SRC/${APP_LIGHT}.generated.json" "$SRC/${APP_DARK}.generated.json"; do
    jq -e '
      ["canvas", "chrome", "surface", "popover", "raised", "hover", "selected", "border",
       "input", "foreground", "muted", "dim", "accent", "accent_foreground", "solid",
       "solid_foreground", "ring", "working", "blocked", "done", "idle", "unknown"]
      | . as $want
      | (($c | keys) == ($want | sort)) and
        (map(. as $k | $c[$k] // "" | test("^#[0-9a-fA-F]{6}$")) | all)
    ' --argjson c "$(jq '.colors' "$file")" -n > /dev/null
  done
}

# The generator writes the suffix and sync globs for it; the two spellings must agree,
# or sync links nothing and herdr-app silently falls back to its own theme.
@test "generator suffix matches HERDR_APP_THEME_SUFFIX" {
  local suffix gen_suffix
  suffix=$(sed -n 's/^HERDR_APP_THEME_SUFFIX="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  gen_suffix=$(sed -n 's/^SUFFIX = "\(.*\)"$/\1/p' "$REPO_ROOT/bin/local/generate-herdr-app-themes")
  [ -n "$suffix" ]
  [ "$suffix" = "$gen_suffix" ]
}
