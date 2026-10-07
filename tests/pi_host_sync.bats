#!/usr/bin/env bats
# Tests for the sync steps that keep the pi host and its package clones in
# step: step_update_pi moves the host, step_pi_verify reports what survived.
#
# step_install_pi is gated on `command -v pi`, so it installs a machine that
# has no pi and says "already installed" to every machine that does. Nothing
# else touched the host: a pi installed once stayed at that release while
# step_pi_packages pulled the clones beside it up to upstream main every sync.
# This machine reached twelve releases behind that way, which is the skew
# bin/local/validate-pi-extension-clones exists to catch — manufactured by the
# sync meant to prevent it.
#
# Both updates are non-fatal, which leaves the failure this whole gate exists
# for reachable through the happy path: `pi update` warns, the warning scrolls
# past, and the machine goes on running agents against a host its clones have
# moved beyond. step_pi_verify is what makes the sync's claim true, so its
# ordering is pinned here too.
#
# pi and the validator are both stubbed. What is under test is the argv each
# step sends, how it reads the result, and where it sits in sync_pi — a real
# `pi update` would replace the developer's own pi from a test.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  ARGV="$TMPDIR/pi-argv"
  FAKE_ROOT="$TMPDIR/workbench"
  mkdir -p "$FAKE_ROOT/bin/local"
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


# _stub_validator EXIT — a validate-pi-extension-clones that exits EXIT.
_stub_validator() {
  cat > "$FAKE_ROOT/bin/local/validate-pi-extension-clones" << SCRIPT
#!/usr/bin/env bash
echo "stub validator: \$*"
exit ${1:-0}
SCRIPT
  chmod +x "$FAKE_ROOT/bin/local/validate-pi-extension-clones"
}

# _run_verify — runs step_pi_verify against the stub tree, with BIN_SRC_DIR
# pointed at it after sourcing so the real validator is never invoked.
_run_verify() {
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
    BIN_SRC_DIR="$3/bin"
    step_pi_verify
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh" "$FAKE_ROOT"
}

@test "a clean verification reports host and clones agreeing" {
  _stub_pi 0
  _stub_validator 0
  run _run_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK Pi host and clones agree"* ]]
}

@test "skew found after the updates is reported, not swallowed" {
  # The updates above it are non-fatal, so this is the only thing standing
  # between a failed `pi update` and a machine running agents that quietly
  # lose their tools.
  _stub_pi 0
  _stub_validator 1
  run _run_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Pi host and clones disagree"* ]]
}

@test "verification asks the validator rather than reimplementing it" {
  # A second definition of skew would drift from the one pre-push enforces.
  # The stub proves the step shells out to that script and reads its status.
  _stub_pi 0
  _stub_validator 0
  run _run_verify
  [[ "$output" == *"stub validator:"* ]]
}

@test "a missing validator warns instead of passing silently" {
  # An empty bin/ must not read as a clean machine: the step reports nothing
  # checked rather than reporting agreement it never established.
  _stub_pi 0
  run _run_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Cannot verify Pi clones"* ]]
  [[ "$output" != *"OK Pi host and clones agree"* ]]
}

@test "verification is skipped on a machine without pi" {
  _hide_pi
  run _run_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN pi not found in PATH"* ]]
}

@test "verification runs last in sync_pi, after both updates" {
  # Checking before the updates would report the state the sync was about to
  # change, which is the shape of a green that means nothing.
  run bash -c '
    LIB_SRC_DIR="$1/lib"
    . "$1/lib/env.sh"
    . "$2"
    declare -f sync_pi | grep -n "step_update_pi\|step_pi_packages\|step_pi_verify"
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh"
  [ "$status" -eq 0 ]

  local packages_line verify_line
  packages_line=$(printf '%s\n' "$output" | grep 'step_pi_packages' | head -1 | cut -d: -f1)
  verify_line=$(printf '%s\n' "$output" | grep 'step_pi_verify' | head -1 | cut -d: -f1)
  [ -n "$packages_line" ]
  [ -n "$verify_line" ]
  [ "$verify_line" -gt "$packages_line" ]
}

# The pi steps speak for the user's own pi, wherever sync is started. Started
# inside a repo whose .mise.toml pins pi, the operator's PATH resolved `pi` to
# that pin: `pi update` targeted a binary it cannot move, and the validator
# measured the user's clones against the project's pi and prescribed the wrong
# cure.

# _scoped_pi NAME DIR — a pi in DIR that records NAME and its cwd.
_scoped_pi() {
  mkdir -p "$2"
  cat > "$2/pi" << SCRIPT
#!/usr/bin/env bash
echo "$1 \$PWD" > "$ARGV"
SCRIPT
  chmod +x "$2/pi"
}

# _run_from DIR STEP — runs STEP with cwd DIR and HOME at $TMPDIR/home.
_run_from() {
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
    BIN_SRC_DIR="$3/bin"
    cd "$4"
    "$5"
  ' _ "$REPO_ROOT" "$REPO_ROOT/ai/pi/steps.sh" "$FAKE_ROOT" "$1" "$2"
}

@test "the host update runs pi from HOME, not from where sync started" {
  # A shim resolves its version from the cwd, so the cwd alone decides which
  # pi a shim-only setup updates.
  mkdir -p "$TMPDIR/home" "$TMPDIR/project"
  _scoped_pi user "$BIN"
  PATH="$BIN:$PATH" HOME="$TMPDIR/home" run _run_from "$TMPDIR/project" step_update_pi
  [ "$status" -eq 0 ]
  [ "$(cat "$ARGV")" = "user $TMPDIR/home" ]
}

@test "a project's mise-activated pi is dropped before the host update" {
  # Under `mise activate` the project's tool dir sits ahead of the user's pi
  # and a cd in a child process does not remove it: only mise's hook-env,
  # re-run from HOME, recomputes PATH. The stub mise answers hook-env the way
  # the real one does from a directory with no pin — the user's PATH back.
  mkdir -p "$TMPDIR/home" "$TMPDIR/project"
  _scoped_pi project "$TMPDIR/project-tools"
  _scoped_pi user "$BIN"
  cat > "$BIN/mise" << SCRIPT
#!/usr/bin/env bash
[[ "\$1 \$2 \$3" == "hook-env -s bash" ]] || exit 1
echo 'export PATH="$BIN:/usr/bin:/bin"'
SCRIPT
  chmod +x "$BIN/mise"
  __MISE_DIFF=x PATH="$TMPDIR/project-tools:$BIN:$PATH" HOME="$TMPDIR/home" \
    run _run_from "$TMPDIR/project" step_update_pi
  [ "$status" -eq 0 ]
  [ "$(cat "$ARGV")" = "user $TMPDIR/home" ]
}

@test "verification runs the validator from HOME too" {
  # The validator asks `pi --version`; from inside a pinning repo it reported
  # the project's pi as the machine's.
  mkdir -p "$TMPDIR/home" "$TMPDIR/project"
  _stub_pi 0
  cat > "$FAKE_ROOT/bin/local/validate-pi-extension-clones" << 'SCRIPT'
#!/usr/bin/env bash
echo "validator cwd: $PWD"
SCRIPT
  chmod +x "$FAKE_ROOT/bin/local/validate-pi-extension-clones"
  HOME="$TMPDIR/home" run _run_from "$TMPDIR/project" step_pi_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"validator cwd: $TMPDIR/home"* ]]
}
