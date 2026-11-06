#!/usr/bin/env bats
# Tests for step_herdr_tui_theme — the four [theme] keys in config.toml.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/steps.sh lib/toml_table.py lib/constants.sh lib/config.sh

setup() {
  load 'test_helper'
  common_setup
  . "$REPO_ROOT/lib/portable.sh"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  CFG="$HOME/.config/herdr/config.toml"
  ARGV="$TMPDIR/herdr-argv"
  unset HERDR_ENV HERDR_PANE_ID
  TUI_LIGHT=$(sed -n 's/^HERDR_TUI_THEME_LIGHT="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  TUI_DARK=$(sed -n 's/^HERDR_TUI_THEME_DARK="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  narrow_path_to bash jq python3
  BIN="$BATS_TEST_TMPDIR/narrow-bin"
}

teardown() {
  common_teardown
}

_theme_block() {
  printf '%s\n' \
    "[theme]" \
    "name = \"$TUI_DARK\"" \
    "auto_switch = true" \
    "light_name = \"$TUI_LIGHT\"" \
    "dark_name = \"$TUI_DARK\""
}

_stub_herdr() {
  cat > "$BIN/herdr" << SCRIPT
#!/usr/bin/env bash
echo "\$*" >> "$ARGV"
if [[ "\$1 \$2" == "config check" ]]; then
  printf '%s\n' "\${FAKE_CHECK_OUT:-config: ok}"
  exit \${FAKE_CHECK_EXIT:-0}
fi
if [[ "\$1 \$2" == "server reload-config" ]]; then
  printf '%s' "\${FAKE_RELOAD_OUT:-}"
  exit \${FAKE_RELOAD_EXIT:-0}
fi
exit 0
SCRIPT
  chmod +x "$BIN/herdr"
}

_run() {
  bash -c '
    set -e
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    "$2"
  ' _ "$REPO_ROOT" "$1"
}

@test "herdr absent is a no-op" {
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$CFG" ]
  [ ! -e "$ARGV" ]
}

@test "missing file is created with only the theme block" {
  _stub_herdr
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ "$(cat "$CFG")" = "$(_theme_block)" ]
  [[ "$(cat "$CFG")" != *onboarding* ]]
  [ "$(cat "$ARGV")" = "$(printf 'config check\nserver reload-config')" ]
}

@test "live-file shape keeps onboarding and updates the four keys in place" {
  _stub_herdr
  mkdir -p "$(dirname "$CFG")"
  printf '%s\n' \
    "onboarding = false" \
    "" \
    "[theme]" \
    "name = \"$TUI_LIGHT\"" \
    "auto_switch = false" \
    > "$CFG"
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ "$(cat "$CFG")" = "$(printf '%s\n' \
    "onboarding = false" \
    "" \
    "[theme]" \
    "name = \"$TUI_DARK\"" \
    "auto_switch = true" \
    "light_name = \"$TUI_LIGHT\"" \
    "dark_name = \"$TUI_DARK\"")" ]
}

@test "other content is preserved byte-for-byte outside the four keys" {
  _stub_herdr
  mkdir -p "$(dirname "$CFG")"
  printf '%s\n' \
    "# keep me" \
    "onboarding = false" \
    "" \
    "[keys]" \
    'leader = " "' \
    "" \
    "[theme]" \
    'name = "paper"  # keep' \
    "auto_switch = false" \
    "" \
    "[theme.custom]" \
    'accent = "#ff0000"' \
    "" \
    "[theme.custom.light]" \
    'accent = "#aaaaaa"' \
    "" \
    "[theme.custom.dark]" \
    'accent = "#111111"' \
    > "$CFG"
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  expected=$(printf '%s\n' \
    "# keep me" \
    "onboarding = false" \
    "" \
    "[keys]" \
    'leader = " "' \
    "" \
    "[theme]" \
    "name = \"$TUI_DARK\"  # keep" \
    "auto_switch = true" \
    "light_name = \"$TUI_LIGHT\"" \
    "dark_name = \"$TUI_DARK\"" \
    "" \
    "[theme.custom]" \
    'accent = "#ff0000"' \
    "" \
    "[theme.custom.light]" \
    'accent = "#aaaaaa"' \
    "" \
    "[theme.custom.dark]" \
    'accent = "#111111"')
  [ "$(cat "$CFG")" = "$expected" ]
}

@test "idempotent re-run under WORKBENCH_SYNC prints nothing and does not call herdr" {
  _stub_herdr
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  : > "$ARGV"
  touch -t 202001010000 "$CFG"
  before=$(file_mtime "$CFG")
  before_bytes=$(cat "$CFG")
  WORKBENCH_SYNC=true run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ "$(cat "$CFG")" = "$before_bytes" ]
  [ "$(file_mtime "$CFG")" = "$before" ]
  [ ! -s "$ARGV" ]
}

@test "symlinked config is left alone" {
  _stub_herdr
  mkdir -p "$TMPDIR/dotfiles" "$(dirname "$CFG")"
  printf '%s\n' 'name = "paper"' > "$TMPDIR/dotfiles/config.toml"
  ln -s "$TMPDIR/dotfiles/config.toml" "$CFG"
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ -L "$CFG" ]
  [ "$(cat "$TMPDIR/dotfiles/config.toml")" = 'name = "paper"' ]
  [[ "$output" == *"symlink"* ]]
  [ ! -e "$ARGV" ]
}

@test "unparseable config is left alone" {
  _stub_herdr
  mkdir -p "$(dirname "$CFG")"
  printf '%s\n' "[theme" > "$CFG"
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ "$(cat "$CFG")" = "[theme" ]
  [[ "$output" == *"not valid TOML"* ]]
  [ ! -e "$ARGV" ]
}

@test "config check failure restores the original file" {
  _stub_herdr
  mkdir -p "$(dirname "$CFG")"
  printf '%s\n' "onboarding = false" > "$CFG"
  export FAKE_CHECK_EXIT=1
  export FAKE_CHECK_OUT=$'config: issues found\nunknown theme name …'
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ "$(cat "$CFG")" = "onboarding = false" ]
  [[ "$output" == *"restored"* ]]
  [[ "$output" == *"unknown theme name"* ]]
  [[ "$(cat "$ARGV")" != *reload-config* ]]
  shopt -s nullglob
  extras=("$HOME/.config/herdr"/config.toml.*)
  [ ${#extras[@]} -eq 0 ]
}

@test "config check failure on a missing file removes it again" {
  _stub_herdr
  export FAKE_CHECK_EXIT=1
  export FAKE_CHECK_OUT=$'config: issues found\nunknown theme name …'
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [ ! -e "$CFG" ]
  shopt -s nullglob
  extras=("$HOME/.config/herdr"/config.toml.*)
  [ ${#extras[@]} -eq 0 ]
}

@test "reload with no server is silent" {
  _stub_herdr
  export FAKE_RELOAD_EXIT=1
  export FAKE_RELOAD_OUT='{"error":{"code":"server_not_running"}}'
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [[ "$output" != *"did not reload"* ]]
}

@test "reload other failure warns to run herdr server reload-config" {
  _stub_herdr
  export FAKE_RELOAD_EXIT=1
  export FAKE_RELOAD_OUT='{"error":{"code":"boom"}}'
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  [[ "$output" == *"herdr server reload-config"* ]]
}

@test "successful run leaves no temp siblings" {
  _stub_herdr
  run _run step_herdr_tui_theme
  [ "$status" -eq 0 ]
  listing=$(ls "$HOME/.config/herdr")
  [ "$listing" = "config.toml" ]
}
