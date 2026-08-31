#!/usr/bin/env bats
# Tests for step_install_claude in ai/claude/steps.sh — the fresh-install half of
# the move off the Homebrew cask. The migration
# (tests/migration_claude_native_install.bats) covers machines that already have
# the cask; this covers machines that have nothing.
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
# CURL_FAILS exits non-zero having printed nothing, the way a 404 does under
# `curl -f` — the case run_remote_installer's pipefail guard exists for.
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

# Runs the step against a PATH holding only the stubs and the base system, so
# the developer's own claude and curl are never reached — this machine has both,
# and a leaked claude would make the already-installed branch the only one ever
# exercised. PATH is narrowed after the libs load, since output.sh needs a
# modern bash to source at all.
#
# PATH_OVERRIDE lets a test narrow it further still — /usr/bin carries a real
# curl, so modelling a machine without one means dropping the directory rather
# than the stub.
_run_step() {
  bash -c '
    . "$2/lib/ui.sh"
    . "$2/ai/claude/steps.sh"
    PATH="$1"
    CLAUDE_INSTALL_URL="https://example.invalid/install.sh"
    step_install_claude
  ' _ "${PATH_OVERRIDE:-$STUBS:/usr/bin:/bin}" "$REPO_ROOT"
}

@test "a machine that already has claude installs nothing" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/claude"
  chmod +x "$STUBS/claude"

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"Claude Code already installed"* ]]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "a machine without claude runs the installer" {
  run _run_step
  [ "$status" -eq 0 ]
  [ -f "$INSTALLER_RAN" ]
  [[ "$output" == *"Claude Code installed"* ]]
}

@test "a failed download is not reported as an install" {
  # A pipeline reports its last command's status, so an unguarded `curl | bash`
  # reads a 404 as a completed install and the step announces success having
  # installed nothing.
  export CURL_FAILS=1

  run _run_step
  [ "$status" -eq 1 ]
  [[ "$output" == *"installer failed"* ]]
  [[ "$output" != *"Claude Code installed"* ]]
}

@test "an installer that fails partway is not reported as an install" {
  printf 'touch "$INSTALLER_RAN"\nexit 1\n' > "$INSTALLER_BODY"

  run _run_step
  [ "$status" -eq 1 ]
  [ -f "$INSTALLER_RAN" ]
  [[ "$output" == *"installer failed"* ]]
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
