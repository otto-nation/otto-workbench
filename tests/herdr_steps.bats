#!/usr/bin/env bats
# Tests for ai/herdr/steps.sh — the argv each step sends to herdr, which
# integrations it installs, and that every failure is non-fatal.
#
# herdr is stubbed: a real `herdr update` would replace the developer's own
# binary from a test.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  ARGV="$TMPDIR/herdr-argv"
  export FAKE_PI_AGENT_DIR="$TMPDIR/pi-agent"
  export FAKE_CLAUDE_DIR="$TMPDIR/claude"
}

teardown() {
  common_teardown
}

# _stub_herdr EXIT [STDOUT] — a herdr on PATH that appends its argv (one call
# per line) to $ARGV, prints STDOUT, and exits EXIT.
_stub_herdr() {
  cat > "$BIN/herdr" << SCRIPT
#!/usr/bin/env bash
echo "\$*" >> "$ARGV"
printf '%s' '${2:-}'
exit ${1:-0}
SCRIPT
  chmod +x "$BIN/herdr"
  PATH="$BIN:$PATH"
}

# _hide_herdr — a PATH with no herdr on it.
_hide_herdr() {
  ln -sf "$BASH" "$BIN/bash"
  PATH="$BIN:/usr/bin:/bin"
}

# _run FN — runs one function from ai/herdr/steps.sh with the ui helpers
# stubbed, in its own bash so the step's warn() does not displace bats'.
_run() {
  bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    err()     { echo "ERR $*"; }
    info()    { echo "INFO $*"; }
    skip()    { echo "SKIP $*"; }
    sync_header() { :; }
    install_via_installer() { echo "INSTALLER $*"; }
    PI_AGENT_DIR="$FAKE_PI_AGENT_DIR"
    CLAUDE_DIR="$FAKE_CLAUDE_DIR"
    HERDR_INSTALL_URL="https://herdr.dev/install.sh"
    . "$2"
    "$3"
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/herdr/steps.sh" "$1"
}

@test "install delegates to the vendor installer with the herdr URL" {
  run _run step_install_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"INSTALLER herdr https://herdr.dev/install.sh Herdr"* ]]
}

@test "update runs plain herdr update, never --handoff" {
  _stub_herdr 0
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "update" ]
}

@test "a failing update warns and the step still exits 0" {
  _stub_herdr 1
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"herdr update"* ]]
}

@test "update is skipped with a warning when herdr is absent" {
  _hide_herdr
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"herdr not found"* ]]
  [ ! -e "$ARGV" ]
}

@test "integrations install pi and claude when both config dirs exist" {
  mkdir -p "$FAKE_PI_AGENT_DIR" "$FAKE_CLAUDE_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "$(printf 'integration install pi\nintegration install claude')" ]
}

@test "integrations skip pi when the Pi agent dir is missing" {
  mkdir -p "$FAKE_CLAUDE_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "integration install claude" ]
}

@test "integrations skip claude when the Claude dir is missing" {
  mkdir -p "$FAKE_PI_AGENT_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "integration install pi" ]
}

@test "a failing integration install warns and the step still exits 0" {
  mkdir -p "$FAKE_PI_AGENT_DIR" "$FAKE_CLAUDE_DIR"
  _stub_herdr 1
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"integration install pi"* ]]
  [[ "$output" == *"WARN"*"integration install claude"* ]]
}

@test "sync updates before installing integrations" {
  mkdir -p "$FAKE_PI_AGENT_DIR"
  _stub_herdr 0
  run _run sync_herdr
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "$(printf 'update\nintegration install pi\nmachine list --json')" ]
}

@test "sync leaves a machine without herdr alone" {
  _hide_herdr
  run _run sync_herdr
  [ "$status" -eq 0 ]
  [ ! -e "$ARGV" ]
}

@test "machine note is printed when no machines are registered" {
  _stub_herdr 0 '[]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" == *"herdr: no remote machines registered — add one with: herdr machine add <ssh-host> --label <name>"* ]]
}

@test "machine note is silent when a machine is registered" {
  _stub_herdr 0 '[{"id":"m1","label":"homelab","target":"homelab","session":"default","enabled":true,"selected":false}]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent under WORKBENCH_SYNC=true" {
  _stub_herdr 0 '[]'
  WORKBENCH_SYNC=true run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
  [ ! -e "$ARGV" ]
}

@test "machine note is silent when herdr errors" {
  _stub_herdr 1 '[]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent when herdr prints something that is not JSON" {
  _stub_herdr 0 'not json'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent when herdr is absent" {
  _hide_herdr
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}
