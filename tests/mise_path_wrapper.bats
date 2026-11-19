#!/usr/bin/env bats
# The ~/.local/bin/mise wrapper strips mise-managed PATH entries so mise
# prepends pinned tools at PATH head. Shims are symlinks to that path, so
# the wrapper is the one place that covers `mise`, `mise activate`, and
# every shim.
bats_require_minimum_version 1.5.0
# select-on: mise/path-wrapper.sh mise/steps.sh

setup() {
  load 'test_helper'
  common_setup
  FAKE_HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$FAKE_HOME/.local/bin" "$FAKE_HOME/.local/share/mise/bin" \
    "$FAKE_HOME/.local/share/mise/shims" "$FAKE_HOME/.local/share/mise/installs/jq/1.7/bin"
  REAL_MISE="$FAKE_HOME/.local/share/mise/bin/mise"
  WRAPPER_SRC="$REPO_ROOT/mise/path-wrapper.sh"
}

teardown() {
  common_teardown
}

# A stand-in for the real mise binary: prints argv0 and PATH, then exits 0.
_install_fake_real() {
  printf '%s\n' '#!/usr/bin/env bash' 'printf "argv0=%s\n" "$0"' 'printf "PATH=%s\n" "$PATH"' > "$REAL_MISE"
  chmod +x "$REAL_MISE"
}

_source_sync() {
  HOME="$FAKE_HOME"
  export HOME WORKBENCH_DIR="$REPO_ROOT" WORKBENCH_STABLE_DIR="$REPO_ROOT" NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/mise/steps.sh"
  # constants.sh already ran under common_setup's HOME; point the mise paths
  # at the fake home so this test cannot touch the machine's mise install.
  # shellcheck disable=SC2034  # consumed by sourced sync_mise
  LOCAL_BIN_DIR="$FAKE_HOME/.local/bin"
  MISE_DATA_ROOT="$FAKE_HOME/.local/share/mise"
  # shellcheck disable=SC2034
  MISE_REAL_BIN="$MISE_DATA_ROOT/bin/mise"
  # shellcheck disable=SC2034
  MISE_WRAPPER_SRC="$REPO_ROOT/mise/path-wrapper.sh"
}

@test "wrapper strips mise installs, shims and command-wrappers from PATH" {
  _install_fake_real
  chmod +x "$WRAPPER_SRC"

  local shims installs wrappers start
  shims="$FAKE_HOME/.local/share/mise/shims"
  installs="$FAKE_HOME/.local/share/mise/installs/jq/1.7/bin"
  wrappers="$FAKE_HOME/.local/share/mise/command-wrappers/bin"
  mkdir -p "$wrappers"
  start="$FAKE_HOME/.local/bin:$shims:$installs:$wrappers:/usr/bin"

  run env HOME="$FAKE_HOME" PATH="$start" MISE_REAL_BIN="$REAL_MISE" \
    "$WRAPPER_SRC"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PATH="* ]]
  [[ "$output" != *"mise/shims"* ]]
  [[ "$output" != *"mise/installs"* ]]
  [[ "$output" != *"mise/command-wrappers"* ]]
  [[ "$output" == *"$FAKE_HOME/.local/bin"* ]]
}

@test "wrapper invoked via a shim still strips PATH and execs the real binary" {
  # argv0 is preserved with `exec -a "$0"` so the real (ELF) mise sees the shim
  # name. A bash stand-in ignores -a, so this test holds the exec line and that
  # a shim-path invocation still reaches the real binary.
  _install_fake_real
  chmod +x "$WRAPPER_SRC"
  grep -q 'exec -a "$0"' "$WRAPPER_SRC"
  local shim
  shim="$FAKE_HOME/.local/share/mise/shims/python3"
  ln -s "$WRAPPER_SRC" "$shim"

  run env HOME="$FAKE_HOME" PATH="$FAKE_HOME/.local/bin:/usr/bin" MISE_REAL_BIN="$REAL_MISE" \
    "$shim"

  [ "$status" -eq 0 ]
  [[ "$output" == *"PATH="* ]]
}

@test "sync_mise relocates a binary at ~/.local/bin/mise and installs the wrapper" {
  _source_sync
  printf '%s\n' '#!/usr/bin/env bash' 'echo relocated-binary' > "$FAKE_HOME/.local/bin/mise"
  chmod +x "$FAKE_HOME/.local/bin/mise"

  sync_mise

  [ -x "$FAKE_HOME/.local/share/mise/bin/mise" ]
  grep -q 'echo relocated-binary' "$FAKE_HOME/.local/share/mise/bin/mise"
  grep -q 'mise-path-wrapper' "$FAKE_HOME/.local/bin/mise"
  [ -x "$FAKE_HOME/.local/bin/mise" ]
}

@test "sync_mise restores the wrapper when a binary has overwritten it" {
  _source_sync
  printf '%s\n' '#!/usr/bin/env bash' 'echo first' > "$FAKE_HOME/.local/bin/mise"
  chmod +x "$FAKE_HOME/.local/bin/mise"
  sync_mise
  printf '%s\n' '#!/usr/bin/env bash' 'echo upgraded' > "$FAKE_HOME/.local/bin/mise"
  chmod +x "$FAKE_HOME/.local/bin/mise"

  sync_mise

  grep -q 'echo upgraded' "$FAKE_HOME/.local/share/mise/bin/mise"
  grep -q 'mise-path-wrapper' "$FAKE_HOME/.local/bin/mise"
}

@test "sync_mise is a no-op when the wrapper and real binary are already in place" {
  _source_sync
  printf '%s\n' '#!/usr/bin/env bash' 'echo first' > "$FAKE_HOME/.local/bin/mise"
  chmod +x "$FAKE_HOME/.local/bin/mise"
  sync_mise
  grep -q 'mise-path-wrapper' "$FAKE_HOME/.local/bin/mise"
  local before real_before
  before="$(cat "$FAKE_HOME/.local/bin/mise")"
  real_before="$(cat "$FAKE_HOME/.local/share/mise/bin/mise")"

  run sync_mise

  [ "$status" -eq 0 ]
  [ "$(cat "$FAKE_HOME/.local/bin/mise")" = "$before" ]
  [ "$(cat "$FAKE_HOME/.local/share/mise/bin/mise")" = "$real_before" ]
}
