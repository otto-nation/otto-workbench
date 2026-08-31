#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260831-claude-native-install.sh — swaps the
# Homebrew claude-code cask for Claude Code's own installer.
#
# The property worth pinning is the order: the native launcher has to prove it
# runs before the cask is taken away. Every failure path below asserts the cask
# survives, because a machine left with neither copy cannot run the tool this
# workbench is built around, and this migration runs unattended.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260831-claude-native-install.sh"
  TMPDIR="$(mktemp -d)"
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"

  # Exported, because the stubs and the script the migration pipes to bash are
  # separate processes that read them from the environment.
  export CASK_MARKER="$TMPDIR/cask-installed"
  export NATIVE_BIN="$TMPDIR/home/.local/bin/claude"
  export INSTALLER_BODY="$TMPDIR/installer.sh"
  export INSTALLER_RAN="$TMPDIR/installer-ran"
  export INSTALLED_BIN_EXIT=0

  _write_stubs
  _installer_writes_launcher
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_stubs — a brew that answers from a marker file and a curl that prints
# the script at INSTALLER_BODY. Never the real ones: this machine has the cask
# the migration removes, so a leaked brew would make the run destructive and
# its result a property of the developer's laptop.
_write_stubs() {
  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
# Only claude-code is known here, so a migration that reached for another cask
# fails the test rather than quietly passing.
[[ "$3" == "claude-code" ]] || exit 1
case "$1" in
  list)
    [[ -f "$CASK_MARKER" ]]
    ;;
  uninstall)
    if [[ -n "${BREW_UNINSTALL_FAILS:-}" ]]; then
      exit 1
    fi
    rm -f "$CASK_MARKER"
    ;;
  *)
    exit 1
    ;;
esac
EOF

  cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
# CURL_FAILS exits non-zero having printed nothing, the way a 404 does under
# `curl -f` — the case the migration's pipefail guard exists for.
if [[ -n "${CURL_FAILS:-}" ]]; then
  exit 22
fi
cat "$INSTALLER_BODY"
EOF

  chmod +x "$STUBS/brew" "$STUBS/curl"
}

# _installer_writes_launcher — the normal installer: leaves a launcher whose
# exit status INSTALLED_BIN_EXIT decides, so a launcher that will not start is
# one variable away.
_installer_writes_launcher() {
  cat > "$INSTALLER_BODY" <<'EOF'
touch "$INSTALLER_RAN"
mkdir -p "$(dirname "$NATIVE_BIN")"
printf '#!/bin/sh\nexit %s\n' "$INSTALLED_BIN_EXIT" > "$NATIVE_BIN"
chmod +x "$NATIVE_BIN"
EOF
}

# _installer_writes_nothing — exits 0 having installed no launcher.
_installer_writes_nothing() {
  printf 'touch "$INSTALLER_RAN"\n' > "$INSTALLER_BODY"
}

_have_cask() { : > "$CASK_MARKER"; }

# Runs the migration with the ui.sh helpers stubbed out. lib/migrations.sh is
# sourced for MIGRATION_NOOP, the way the framework provides it. PATH is
# narrowed after the libs are loaded rather than before: the outer bash has to
# be a modern one for output.sh's 4.3 guard, and only the migration's own
# lookups need confining to the stubs.
#
# PATH_OVERRIDE lets a test drop the stubs to model a machine without Homebrew.
_run_migration() {
  bash -c '
    info()    { echo "INFO $*"; }
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    BIN_SRC_DIR="$2/bin"
    LEGACY_WORKBENCH_ROOT="$5"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    CLAUDE_NATIVE_BIN="$3"
    CLAUDE_INSTALL_URL="https://example.invalid/install.sh"
    PATH="$4"
    migration_20260831_claude_native_install
  ' _ "$MIGRATION" "$REPO_ROOT" "$NATIVE_BIN" \
    "${PATH_OVERRIDE:-$STUBS:/usr/bin:/bin}" "$TMPDIR/.unused-legacy"
}

@test "a machine without Homebrew is a no-op" {
  PATH_OVERRIDE="/usr/bin:/bin" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a machine without the cask is a no-op" {
  run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "installs the native launcher and removes the cask" {
  _have_cask

  run _run_migration
  [ "$status" -eq 0 ]
  [ -x "$NATIVE_BIN" ]
  [ ! -e "$CASK_MARKER" ]
  [[ "$output" == *"removed the Homebrew cask"* ]]
}

@test "a launcher already in place is not reinstalled" {
  _have_cask
  mkdir -p "$(dirname "$NATIVE_BIN")"
  printf '#!/bin/sh\nexit 0\n' > "$NATIVE_BIN"
  chmod +x "$NATIVE_BIN"

  run _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$CASK_MARKER" ]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "a second run is a no-op" {
  _have_cask

  run _run_migration
  [ "$status" -eq 0 ]
  run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a launcher that will not run leaves the cask in place" {
  # The installer can exit 0 having written something that does not start. The
  # cask is the only other copy on the machine at that moment.
  _have_cask
  export INSTALLED_BIN_EXIT=1

  run _run_migration
  [ "$status" -eq 1 ]
  [ -f "$CASK_MARKER" ]
  [[ "$output" == *"does not run"* ]]
}

@test "an installer that writes no launcher leaves the cask in place" {
  _have_cask
  _installer_writes_nothing

  run _run_migration
  [ "$status" -eq 1 ]
  [ -f "$CASK_MARKER" ]
}

@test "a failed download is reported as a failed download" {
  # A pipeline reports its last command's status, so an unguarded
  # `curl | bash` reads a 404 as a successful install and blames the launcher
  # that was never written. The cask survives either way; the message is what
  # tells the operator which half broke.
  _have_cask
  export CURL_FAILS=1

  run _run_migration
  [ "$status" -eq 1 ]
  [ -f "$CASK_MARKER" ]
  [[ "$output" == *"installer failed"* ]]
}

@test "a cask that cannot be removed reports the manual command" {
  _have_cask
  export BREW_UNINSTALL_FAILS=1

  run _run_migration
  [ "$status" -eq 1 ]
  [ -f "$CASK_MARKER" ]
  [[ "$output" == *"brew uninstall --cask claude-code"* ]]
}
