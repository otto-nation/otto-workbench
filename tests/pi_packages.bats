#!/usr/bin/env bats
# Tests for step_pi_packages in ai/pi/steps.sh — refreshing the git package
# clones Pi resolves from settings.json.
#
# Pi reuses a `git:` clone forever once it exists, and `pi update` with no
# target updates only the host binary, so nothing moved those clones and a new
# pi ran against an old provider extension. The silent direction is what makes
# this worth a sync step: a provider predating pi 0.86's TranscriptContext
# sends no system prompt and no tool declarations, so the agent cannot call a
# tool and the run exits 0 having done nothing.
#
# pi is stubbed. What is under test is the argv the step sends and how it reads
# the result — a real `pi update` would reach the network and refresh the
# developer's own clones from a test.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  ARGV="$TMPDIR/pi-argv"
}

teardown() {
  common_teardown
}

# _stub_pi EXIT — a pi on PATH that records its argv and exits EXIT.
_stub_pi() {
  cat > "$BIN/pi" << SCRIPT
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$ARGV"
exit ${1:-0}
SCRIPT
  chmod +x "$BIN/pi"
  PATH="$BIN:$PATH"
}

# _hide_pi — a PATH with no pi on it at all.
_hide_pi() {
  ln -sf "$BASH" "$BIN/bash"
  PATH="$BIN:/usr/bin:/bin"
}

# _run_step — runs step_pi_packages with the ui helpers stubbed, in its own
# bash so the step's own warn() does not displace bats'.
_run_step() {
  bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    err()     { echo "ERR $*"; }
    info()    { echo "INFO $*"; }
    skip()    { echo "SKIP $*"; }
    LIB_SRC_DIR="$1/lib"
    . "$1/lib/env.sh"
    . "$1/ai/pi/steps.sh"
    step_pi_packages
  ' _ "$REPO_ROOT"
}

@test "refreshes packages rather than the host binary" {
  # `pi update` with no target is --self, which updates pi and leaves every
  # clone where it was. That default is the whole reason the clones drifted.
  _stub_pi 0
  run _run_step
  [ "$status" -eq 0 ]
  grep -qx -- "--extensions" "$ARGV"
  ! grep -qx -- "--self" "$ARGV"
}

@test "does not adopt the packages of whatever repo sync runs from" {
  # Sync speaks for this machine's user scope. Without --no-approve a sync run
  # from inside a repo carrying .pi/settings.json would trust and update that
  # project's packages too, which is the operator's call.
  _stub_pi 0
  run _run_step
  [ "$status" -eq 0 ]
  grep -qx -- "--no-approve" "$ARGV"
  ! grep -qx -- "--approve" "$ARGV"
}

@test "reports success when the refresh worked" {
  _stub_pi 0
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK Pi packages refreshed"* ]]
}

@test "a failed refresh warns with the command to run by hand" {
  # Non-fatal on purpose: offline, or a forge that is down, should still let
  # the rest of the sync apply. But it has to say so, because the condition it
  # was there to prevent is invisible from the outside.
  _stub_pi 1
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"pi update --extensions"* ]]
}

@test "the refresh is wired into sync_pi, not only defined" {
  # A step nothing calls is a step that never runs. sync_pi is what
  # `otto-workbench sync` drives, and the drift this fixes only stays fixed if
  # the refresh happens on every sync rather than when someone remembers.
  run grep -q 'step_pi_packages' "$REPO_ROOT/ai/pi/steps.sh"
  [ "$status" -eq 0 ]

  # Inside sync_pi's body specifically, not merely somewhere in the file.
  run bash -c "sed -n '/^sync_pi()/,/^}/p' '$REPO_ROOT/ai/pi/steps.sh' | grep -q step_pi_packages"
  [ "$status" -eq 0 ]
}

@test "a machine without pi is skipped rather than failed" {
  # Same guard sync_pi itself carries: sync re-applies config and does not
  # install a tool the operator never chose.
  _hide_pi
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN pi not found in PATH"* ]]
  [ ! -f "$ARGV" ]
}
