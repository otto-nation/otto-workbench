#!/usr/bin/env bats
# Tests for step_update_pi in ai/pi/steps.sh — moving the installed pi forward
# on every sync.
#
# step_install_pi is gated on `command -v pi`, so it installs a machine that
# has no pi and says "already installed" to every machine that does. Nothing
# else touched the host: a pi installed once stayed at that release while
# step_pi_packages pulled the clones beside it up to upstream main every sync.
# This machine reached twelve releases behind that way, which is the skew
# bin/local/validate-pi-extension-clones exists to catch — manufactured by the
# sync meant to prevent it.
#
# pi is stubbed. What is under test is the argv the step sends, how it reads
# the result, and where it sits relative to the package refresh — a real
# `pi update` would replace the developer's own pi from a test.
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

# _run_step — runs step_update_pi with the ui helpers stubbed, in its own bash
# so the step's own warn() does not displace bats'.
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
    . "$2"
    step_update_pi
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh"
}

@test "updates the host binary and not the clones" {
  # The package refresh is step_pi_packages' job and runs next. A step that
  # passed --extensions here would update the clones twice and the host never,
  # which is the state this step exists to end.
  _stub_pi 0
  run _run_step
  [ "$status" -eq 0 ]
  [ -f "$ARGV" ]
  run grep -qx -- "--extensions" "$ARGV"
  [ "$status" -ne 0 ]
}

@test "reports success when the update worked" {
  _stub_pi 0
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK Pi is current"* ]]
}

@test "a failed update warns with the command to run by hand" {
  # Non-fatal on purpose: offline should still let the rest of the sync apply.
  # But it has to say so — a host left behind is invisible from the outside,
  # and the validator is the only other thing that will mention it.
  _stub_pi 1
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"pi update"* ]]
}

@test "a machine without pi is skipped rather than installed onto" {
  # Updating is not installing. sync re-applies config for tools the operator
  # already chose; step_install_pi is where a missing pi is handled.
  _hide_pi
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN pi not found in PATH"* ]]
  [ ! -f "$ARGV" ]
}

@test "the host update runs before the package refresh in sync_pi" {
  # Order is the point, not merely presence. Refreshing clones against the old
  # host and only then moving the host leaves exactly one sync's worth of skew
  # behind every time — the clones would sit ahead of the pi that just
  # replaced the one they were fetched for.
  #
  # `declare -f` asks bash for the function's real boundaries rather than
  # guessing from a closing brace at column 0.
  run bash -c '
    LIB_SRC_DIR="$1/lib"
    . "$1/lib/env.sh"
    . "$2"
    declare -f sync_pi | grep -n "step_update_pi\|step_pi_packages"
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh"
  [ "$status" -eq 0 ]

  local update_line packages_line
  update_line=$(printf '%s\n' "$output" | grep 'step_update_pi' | head -1 | cut -d: -f1)
  packages_line=$(printf '%s\n' "$output" | grep 'step_pi_packages' | head -1 | cut -d: -f1)
  [ -n "$update_line" ]
  [ -n "$packages_line" ]
  [ "$update_line" -lt "$packages_line" ]
}

@test "the host update is registered as an install step too" {
  # `otto-workbench install` on a machine that already has a years-old pi must
  # not end with that pi still in place: step_install_pi reports it as already
  # installed and does nothing.
  run bash -c '
    LIB_SRC_DIR="$1/lib"
    . "$1/lib/env.sh"
    . "$2"
    declare -f register_pi_steps | grep -q step_update_pi
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh"
  [ "$status" -eq 0 ]
}
