#!/usr/bin/env bats
# Tests for install_cask in lib/setup.sh — the shared Homebrew-cask install used
# by terminals/ghostty/steps.sh and docker/orbstack/setup.sh.
#
# Ghostty stands in for both here: the helper takes the tool, the cask, the label
# and the manual-install URL as arguments and does nothing else with them, so a
# second set of arguments would exercise the same branches.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"

  # Exported, because the stub reads them from the environment.
  export BREW_LOG="$TMPDIR/brew-args"

  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
# Records the arguments so a test can assert the cask actually asked for, and
# fails on demand to model a download or install that does not complete.
echo "$*" >> "$BREW_LOG"
if [[ -n "${BREW_INSTALL_FAILS:-}" ]]; then
  exit 1
fi
EOF
  chmod +x "$STUBS/brew"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Runs install_cask against a PATH holding only the stubs and the base system,
# so the developer's own brew and ghostty are never reached. PATH is narrowed
# after lib/ui.sh loads, since output.sh needs a modern bash to source at all.
#
# PATH_OVERRIDE narrows it further still, to model a machine without Homebrew.
_run_install_cask() {
  bash -c '
    . "$2/lib/ui.sh"
    PATH="$1"
    install_cask ghostty ghostty Ghostty https://ghostty.org
  ' _ "${PATH_OVERRIDE:-$STUBS:/usr/bin:/bin}" "$REPO_ROOT"
}

@test "a tool already in PATH is not reinstalled" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/ghostty"
  chmod +x "$STUBS/ghostty"

  run _run_install_cask
  [ "$status" -eq 0 ]
  [[ "$output" == *"ghostty already installed"* ]]
  [ ! -e "$BREW_LOG" ]
}

@test "a missing tool is installed from the named cask" {
  run _run_install_cask
  [ "$status" -eq 0 ]
  [[ "$output" == *"Installing Ghostty"* ]]
  [[ "$output" == *"Ghostty installed"* ]]
  [[ "$(cat "$BREW_LOG")" == "install --cask ghostty" ]]
}

@test "a machine without Homebrew is told how to install by hand" {
  # An empty PATH rather than a deleted stub: /opt/homebrew/bin carries a real
  # brew on this machine, and reaching it would install the cask for real.
  mkdir -p "$TMPDIR/empty"
  PATH_OVERRIDE="$TMPDIR/empty" run _run_install_cask
  [ "$status" -eq 1 ]
  [[ "$output" == *"Homebrew not found"* ]]
  [[ "$output" == *"https://ghostty.org"* ]]
  [ ! -e "$BREW_LOG" ]
}

@test "a failed brew install is not reported as an install" {
  export BREW_INSTALL_FAILS=1

  run _run_install_cask
  [ "$status" -eq 1 ]
  [[ "$output" != *"Ghostty installed"* ]]
  [[ "$output" == *"https://ghostty.org"* ]]
}
