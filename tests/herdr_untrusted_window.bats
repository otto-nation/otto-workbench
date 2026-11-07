#!/usr/bin/env bats
# Tests for the untrusted herdr-app window — settings derivation, keys/themes/
# shared seed, preflight, and herdr-app-untrusted.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/steps.sh ai/herdr/untrusted_window.sh ai/herdr/bin/herdr-app-untrusted lib/files.sh lib/config.sh lib/constants.sh

setup() {
  load 'test_helper'
  common_setup
  . "$REPO_ROOT/lib/portable.sh"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  unset XDG_STATE_HOME
  unset HERDR_ENV HERDR_PANE_ID
  narrow_path_to bash jq
  BIN="$BATS_TEST_TMPDIR/narrow-bin"
  MAIN="$HOME/.config/herdr-app"
  UNTRUSTED="$HOME/.config/herdr-app-untrusted"
  STATE="$HOME/.local/state/herdr-core"
  OPEN_ARGV="$TMPDIR/open-argv"
  SESSION=$(sed -n 's/^HERDR_UNTRUSTED_SESSION="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  SRC="$REPO_ROOT/ai/herdr/app/themes"
  APP_DARK=$(sed -n 's/^HERDR_APP_THEME_DARK="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
}

teardown() {
  common_teardown
}

_run_step() {
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

_run_launcher() {
  env NO_COLOR=1 HOME="$HOME" WORKBENCH_STABLE_DIR="$REPO_ROOT" PATH="$BIN:/usr/bin:/bin" \
    "$REPO_ROOT/ai/herdr/bin/herdr-app-untrusted" "$@"
}

_stub_open() {
  cat > "$BIN/open" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$OPEN_ARGV"
exit 0
EOF
  chmod +x "$BIN/open"
}

_seed_main() {
  mkdir -p "$MAIN"
  if [[ $# -gt 0 ]]; then
    printf '%s\n' "$1" > "$MAIN/settings.json"
  else
    printf '%s\n' '{"theme":{"light":"gruvbox-light","dark":"gruvbox-dark"},"clipboard_write":true}' > "$MAIN/settings.json"
  fi
}

_seed_untrusted() {
  mkdir -p "$UNTRUSTED"
  if [[ $# -gt 0 ]]; then
    printf '%s\n' "$1" > "$UNTRUSTED/settings.json"
  fi
}

_write_json() {
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "$2" > "$1"
}

@test "HERDR_UNTRUSTED_SESSION is untrusted (homelab setup-herdr-untrusted-window.sh --session default)" {
  [ "$SESSION" = "untrusted" ]
  dir=$(sed -n 's/^HERDR_UNTRUSTED_APP_DIR="\$HOME\/\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ "$dir" = ".config/herdr-app-untrusted" ]
}

@test "absent untrusted dir is a no-op" {
  _seed_main
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$UNTRUSTED" ]
}

@test "settings copy appearance keys from main, force clipboard_write false, keep zoom" {
  _seed_main '{"theme":{"light":"paper","dark":"ink"},"font":"MainFont","font_size":16,"zoom":1,"clipboard_write":true,"telemetry":false}'
  _seed_untrusted '{"theme":"moss","font":"Other","zoom":1.5,"clipboard_write":true}'
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ "$(jq -c '.theme' "$UNTRUSTED/settings.json")" = '{"light":"paper","dark":"ink"}' ]
  [ "$(jq -r '.font' "$UNTRUSTED/settings.json")" = "MainFont" ]
  [ "$(jq -r '.font_size' "$UNTRUSTED/settings.json")" = "16" ]
  [ "$(jq -r '.zoom' "$UNTRUSTED/settings.json")" = "1.5" ]
  [ "$(jq -r '.clipboard_write' "$UNTRUSTED/settings.json")" = "false" ]
  [ "$(jq -r 'has("telemetry")' "$UNTRUSTED/settings.json")" = "false" ]
}

@test "malformed main settings is warned about and leaves the untrusted file" {
  _seed_untrusted '{"zoom":1.4,"clipboard_write":true}'
  local raw
  for raw in '{not json' '' '[]' '{}{}'; do
    mkdir -p "$MAIN"
    printf '%s' "$raw" > "$MAIN/settings.json"
    run _run_step step_herdr_untrusted_window
    [ "$status" -eq 0 ]
    [ "$(cat "$UNTRUSTED/settings.json")" = '{"zoom":1.4,"clipboard_write":true}' ]
    [[ "$output" == *"not a JSON object"* || "$output" == *"missing"* ]]
  done
}

@test "missing main settings is warned about and leaves the untrusted file" {
  _seed_untrusted '{"zoom":1.4,"clipboard_write":true}'
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ "$(cat "$UNTRUSTED/settings.json")" = '{"zoom":1.4,"clipboard_write":true}' ]
  [[ "$output" == *"missing"* ]]
}

@test "symlinked untrusted settings.json is left alone" {
  _seed_main
  mkdir -p "$UNTRUSTED" "$TMPDIR/dotfiles"
  printf '%s\n' '{"theme":"paper","clipboard_write":true}' > "$TMPDIR/dotfiles/settings.json"
  ln -s "$TMPDIR/dotfiles/settings.json" "$UNTRUSTED/settings.json"
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -L "$UNTRUSTED/settings.json" ]
  [ "$(cat "$TMPDIR/dotfiles/settings.json")" = '{"theme":"paper","clipboard_write":true}' ]
  [[ "$output" == *"symlink"* ]]
}

@test "keys.json is a symlink to main" {
  _seed_main
  _seed_untrusted
  printf '%s\n' '{"bindings":[]}' > "$MAIN/keys.json"
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -L "$UNTRUSTED/keys.json" ]
  [ "$(readlink "$UNTRUSTED/keys.json")" = "$MAIN/keys.json" ]
}

@test "differing regular keys.json is left with a warning" {
  _seed_main
  _seed_untrusted
  printf '%s\n' '{"bindings":["main"]}' > "$MAIN/keys.json"
  printf '%s\n' '{"bindings":["untrusted"]}' > "$UNTRUSTED/keys.json"
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ ! -L "$UNTRUSTED/keys.json" ]
  [ "$(cat "$UNTRUSTED/keys.json")" = '{"bindings":["untrusted"]}' ]
  [[ "$output" == *"differs from"* ]]
}

@test "identical regular keys.json is replaced by a symlink" {
  _seed_main
  _seed_untrusted
  printf '%s\n' '{"bindings":[]}' > "$MAIN/keys.json"
  printf '%s\n' '{"bindings":[]}' > "$UNTRUSTED/keys.json"
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -L "$UNTRUSTED/keys.json" ]
}

@test "shared.json is written only when absent" {
  _seed_main
  _seed_untrusted
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ "$(jq -c . "$UNTRUSTED/shared.json")" = '{"frame.welcomed":"skipped","layout":"main"}' ]
  printf '%s\n' '{"frame.welcomed":"finished"}' > "$UNTRUSTED/shared.json"
  WORKBENCH_SYNC=true run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ "$(cat "$UNTRUSTED/shared.json")" = '{"frame.welcomed":"finished"}' ]
}

@test "themes are linked into the untrusted home" {
  _seed_main
  _seed_untrusted
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -L "$UNTRUSTED/themes/${APP_DARK}.json" ]
  [ "$(readlink "$UNTRUSTED/themes/${APP_DARK}.json")" = "$SRC/${APP_DARK}.json" ]
}

@test "re-run under WORKBENCH_SYNC prints nothing and does not rewrite settings" {
  _seed_main
  _seed_untrusted
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  touch -t 202001010000 "$UNTRUSTED/settings.json"
  before=$(file_mtime "$UNTRUSTED/settings.json")
  before_bytes=$(cat "$UNTRUSTED/settings.json")
  WORKBENCH_SYNC=true run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ "$(cat "$UNTRUSTED/settings.json")" = "$before_bytes" ]
  [ "$(file_mtime "$UNTRUSTED/settings.json")" = "$before" ]
}

@test "finished terminal with empty afar passes preflight" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t1","state":"finished"}}}'
  _stub_open
  run _run_launcher
  [ "$status" -eq 0 ]
  [ -f "$OPEN_ARGV" ]
}

@test "live terminal fails preflight and launcher does not call open" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t1","state":"live"}}}'
  _stub_open
  run _run_launcher
  [ "$status" -ne 0 ]
  [ ! -e "$OPEN_ARGV" ]
  [[ "$output" == *"hc kill t1"* ]]
  [[ "$output" == *"hc rm t1"* ]]
}

@test "non-empty afar fails preflight" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t1","state":"finished","afar":["abc"]}}}'
  _stub_open
  run _run_launcher
  [ "$status" -ne 0 ]
  [ ! -e "$OPEN_ARGV" ]
  [[ "$output" == *"afar"* ]]
}

@test "machine id overlap fails preflight" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/default/machines.json" '[{"name":"homelab","id":"same-id","target":"h"}]'
  _write_json "$STATE/$SESSION/machines.json" '[{"name":"agent","id":"same-id","target":"a"}]'
  _stub_open
  run _run_launcher
  [ "$status" -ne 0 ]
  [ ! -e "$OPEN_ARGV" ]
  [[ "$output" == *"same-id"* ]]
}

@test "empty XDG_STATE_HOME falls back to ~/.local/state like unset" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t1","state":"live"}}}'
  _stub_open
  XDG_STATE_HOME='' run _run_launcher
  [ "$status" -ne 0 ]
  [[ "$output" == *"hc kill t1"* ]]
}

@test "XDG_STATE_HOME selects the untrusted core dir" {
  _seed_main
  _seed_untrusted
  other="$TMPDIR/xdg-state"
  _write_json "$other/herdr-core/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t9","state":"live"}}}'
  _stub_open
  XDG_STATE_HOME="$other" run _run_launcher
  [ "$status" -ne 0 ]
  [[ "$output" == *"hc kill t9"* ]]
}

@test "launcher open argv carries HC_SESSION and HERDR_APP_HOME" {
  _seed_main
  _stub_open
  run _run_launcher
  [ "$status" -eq 0 ]
  [ "$(cat "$OPEN_ARGV")" = "$(printf '%s\n' -n -a Herdr --env "HC_SESSION=$SESSION" --env "HERDR_APP_HOME=$UNTRUSTED")" ]
  [ -d "$UNTRUSTED" ]
  [ "$(jq -r '.clipboard_write' "$UNTRUSTED/settings.json")" = "false" ]
}

@test "sync warns on a live terminal and still exits 0" {
  _seed_main
  _seed_untrusted
  _write_json "$STATE/$SESSION/terminals.json" '{"next_id":2,"terminals":{"1":{"address":"t1","state":"live"}}}'
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [[ "$output" == *"hc kill t1"* ]]
  [[ "$output" == *"will refuse to open"* ]]
}

@test "clipboard_write true is re-reconciled to false" {
  _seed_main '{"theme":"paper","clipboard_write":true}'
  _seed_untrusted '{"theme":"paper","clipboard_write":true,"zoom":2}'
  run _run_step step_herdr_untrusted_window
  [ "$status" -eq 0 ]
  [ "$(jq -r '.clipboard_write' "$UNTRUSTED/settings.json")" = "false" ]
  [ "$(jq -r '.zoom' "$UNTRUSTED/settings.json")" = "2" ]
}
