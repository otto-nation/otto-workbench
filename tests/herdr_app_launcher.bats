#!/usr/bin/env bats
# Tests for ai/herdr/bin/herdr-app — opens the main window through open(1)
# instead of as a child of the shell, and passes commands to the app's CLI.
#
# The app bundle and open are stubbed: a real launch would open a window on the
# developer's desktop.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/bin/herdr-app lib/constants.sh

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  unset HERDR_APP_HOME HC_SESSION HERDR_APP_BUNDLE
  # Only what the launcher itself runs, so /usr/bin/open on macOS cannot answer
  # for the stub.
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN"
  local tool
  for tool in cat dirname readlink; do
    ln -s "$(command -v "$tool")" "$BIN/$tool"
  done
  BUNDLE="$HOME/Applications/Herdr.app"
  CALLS="$TMPDIR/herdr-calls"
  OPEN_ARGV="$TMPDIR/open-argv"
}

teardown() {
  common_teardown
}

# _stub_bundle STATUS_EXIT — a bundle binary that appends its argv (one call per
# line) to $CALLS and exits STATUS_EXIT for `status`, 0 otherwise.
_stub_bundle() {
  mkdir -p "$BUNDLE/Contents/MacOS"
  cat > "$BUNDLE/Contents/MacOS/Herdr" << SCRIPT
#!$BASH
echo "argv:\$*" >> "$CALLS"
[[ "\${1:-}" == status ]] && exit $1
exit 0
SCRIPT
  chmod +x "$BUNDLE/Contents/MacOS/Herdr"
}

_stub_open() {
  cat > "$BIN/open" << SCRIPT
#!$BASH
printf '%s\n' "\$@" > "$OPEN_ARGV"
SCRIPT
  chmod +x "$BIN/open"
}

_launch() {
  env NO_COLOR=1 PATH="$BIN" "$BASH" "$REPO_ROOT/ai/herdr/bin/herdr-app" "$@"
}

@test "with no window running, opens a new instance of the bundle and returns" {
  _stub_bundle 1
  _stub_open
  run _launch
  [ "$status" -eq 0 ]
  run cat "$OPEN_ARGV"
  [ "$output" = "$(printf '%s\n' -n "$BUNDLE")" ]
  run cat "$CALLS"
  [ "$output" = "argv:status" ]
}

@test "passes HERDR_APP_HOME and HC_SESSION to the launched app when set" {
  _stub_bundle 1
  _stub_open
  HERDR_APP_HOME="$HOME/alt" HC_SESSION=work run _launch
  [ "$status" -eq 0 ]
  run cat "$OPEN_ARGV"
  [ "$output" = "$(printf '%s\n' -n "$BUNDLE" --env "HERDR_APP_HOME=$HOME/alt" --env HC_SESSION=work)" ]
}

@test "a window that answers status is left alone" {
  _stub_bundle 0
  _stub_open
  run _launch
  [ "$status" -eq 0 ]
  [[ "$output" == *"already running"* ]]
  [ ! -e "$OPEN_ARGV" ]
}

@test "arguments go to the bundle binary unchanged, without open" {
  _stub_bundle 1
  _stub_open
  run _launch do layout.switch --name workbench
  [ "$status" -eq 0 ]
  run cat "$CALLS"
  [ "$output" = "argv:do layout.switch --name workbench" ]
  [ ! -e "$OPEN_ARGV" ]
}

@test "without open, runs the bundle binary in the foreground" {
  _stub_bundle 1
  run _launch
  [ "$status" -eq 0 ]
  run cat "$CALLS"
  [ "$output" = "$(printf '%s\n' argv:status argv:)" ]
}

@test "HERDR_APP_BUNDLE points at a bundle installed elsewhere" {
  BUNDLE="$TMPDIR/elsewhere/Herdr.app"
  _stub_bundle 1
  _stub_open
  HERDR_APP_BUNDLE="$BUNDLE" run _launch
  [ "$status" -eq 0 ]
  run cat "$OPEN_ARGV"
  [ "$output" = "$(printf '%s\n' -n "$BUNDLE")" ]
}

@test "a missing bundle fails with the path it looked for" {
  _stub_open
  run _launch
  [ "$status" -eq 1 ]
  [[ "$output" == *"$BUNDLE/Contents/MacOS/Herdr"* ]]
  [ ! -e "$OPEN_ARGV" ]
}

@test "--help prints usage without touching the app" {
  _stub_bundle 1
  _stub_open
  run _launch --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: herdr-app"* ]]
  [ ! -e "$CALLS" ]
  [ ! -e "$OPEN_ARGV" ]
}
