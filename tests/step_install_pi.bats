#!/usr/bin/env bats
# Tests for step_install_pi in ai/pi/steps.sh — the step that makes `pi` exist on
# a machine whose Pi setup previously wrote settings and skills for a binary
# nobody had installed. The shared branch logic lives in install_via_installer
# and is exercised in full by tests/step_install_claude.bats; this covers the
# wiring — the guard command, the URL, and the label Pi's messages carry.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"

  # Exported, because the stub and the script it prints for bash to run are
  # separate processes that read them from the environment.
  export INSTALLER_BODY="$TMPDIR/installer.sh"
  export INSTALLER_RAN="$TMPDIR/installer-ran"

  cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${CURL_FAILS:-}" ]]; then
  exit 22
fi
cat "$INSTALLER_BODY"
EOF
  chmod +x "$STUBS/curl"
  printf 'touch "$INSTALLER_RAN"\n' > "$INSTALLER_BODY"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Runs the step against a PATH holding only the stubs and the base system, so a
# pi or curl the developer happens to have is never reached. PATH is narrowed
# after the libs load, since output.sh needs a modern bash to source at all.
_run_step() {
  bash -c '
    . "$2/lib/ui.sh"
    . "$2/ai/pi/steps.sh"
    PATH="$1"
    PI_INSTALL_URL="https://example.invalid/install.sh"
    step_install_pi
  ' _ "${PATH_OVERRIDE:-$STUBS:/usr/bin:/bin}" "$REPO_ROOT"
}

@test "a machine that already has pi installs nothing" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/pi"
  chmod +x "$STUBS/pi"

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "a machine without pi runs the installer" {
  run _run_step
  [ "$status" -eq 0 ]
  [ -f "$INSTALLER_RAN" ]
  [[ "$output" == *"Pi installed"* ]]
}

@test "a failed install is not reported as an install" {
  export CURL_FAILS=1

  run _run_step
  [ "$status" -eq 1 ]
  [[ "$output" == *"installer failed"* ]]
  [[ "$output" != *"Pi installed"* ]]
}

@test "a machine without curl says how to install by hand" {
  # An empty PATH rather than a deleted stub: /usr/bin carries a real curl, and
  # with that on PATH the step reaches the network and fails one branch further
  # on — passing this assertion without ever running the guard it is named for.
  mkdir -p "$TMPDIR/empty"
  PATH_OVERRIDE="$TMPDIR/empty" run _run_step
  [ "$status" -ne 0 ]
  [[ "$output" == *"curl not found"* ]]
  [[ "$output" == *"https://example.invalid/install.sh"* ]]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "the install step is registered ahead of the config steps" {
  run bash -c '
    . "$1/lib/ui.sh"
    . "$1/ai/pi/steps.sh"
    STEPS=()
    register_pi_steps
    printf "%s\n" "${STEPS[@]}"
  ' _ "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == *"step_install_pi"* ]]
}
