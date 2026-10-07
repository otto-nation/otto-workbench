#!/usr/bin/env bats
# Tests for install_brew_or_mise in lib/setup.sh — the install used by
# ai/rtk/steps.sh and ai/claude/steps.sh for CLI tools that ship as a Homebrew
# formula and as a mise registry tool.
#
# Homebrew is preferred where it exists; mise is the fallback for a machine
# without it, such as an unprivileged user on a Linux host. rtk stands in for
# both callers: the helper does nothing with its arguments beyond passing them
# on, so a second tool would exercise the same branches.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"

  # Exported, because the stubs read them from the environment.
  export BREW_LOG="$TMPDIR/brew-args"
  export MISE_LOG="$TMPDIR/mise-args"
  export MISE_DATA_DIR="$TMPDIR/mise-data"

  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$BREW_LOG"
[[ -z "${BREW_INSTALL_FAILS:-}" ]] || exit 1
# Models a formula that links its binary onto PATH, next to brew itself.
[[ -n "${BREW_LEAVES_NO_BINARY:-}" ]] && exit 0
printf '#!/bin/sh\nexit 0\n' > "$(dirname "$0")/rtk"
chmod +x "$(dirname "$0")/rtk"
EOF
  chmod +x "$STUBS/brew"

  # Models `mise use -g TOOL`: the tool's shim appears under the data dir's
  # shims, which is not on PATH — exactly the state a fresh mise install leaves.
  cat > "$STUBS/mise" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$MISE_LOG"
[[ -z "${MISE_INSTALL_FAILS:-}" ]] || exit 1
mkdir -p "$MISE_DATA_DIR/shims"
printf '#!/bin/sh\nexit 0\n' > "$MISE_DATA_DIR/shims/rtk"
chmod +x "$MISE_DATA_DIR/shims/rtk"
EOF
  chmod +x "$STUBS/mise"
}

teardown() {
  common_teardown
}

# Runs install_brew_or_mise against a PATH of only the chosen stubs and the base
# system, then reports where rtk resolves in the same shell — the property the
# shim PATH update exists for. PATH is narrowed after lib/ui.sh loads, since
# output.sh needs a modern bash to source at all.
_run_install() {
  bash -c '
    . "$2/lib/ui.sh"
    PATH="$1"
    install_brew_or_mise rtk rtk rtk RTK
    status=$?
    echo "RESOLVED=$(command -v rtk || true)"
    exit "$status"
  ' _ "$1" "$REPO_ROOT"
}

# _path_with STUB... — a PATH holding only the named stubs and the base system.
# /opt/homebrew/bin is never on it, so the developer's real brew is unreachable.
_path_with() {
  local dir="$TMPDIR/path-$*" name
  dir="${dir// /-}"
  mkdir -p "$dir"
  for name in "$@"; do ln -sf "$STUBS/$name" "$dir/$name"; done
  echo "$dir:/usr/bin:/bin"
}

@test "a tool already in PATH is not reinstalled" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/rtk"
  chmod +x "$STUBS/rtk"

  run _run_install "$STUBS:/usr/bin:/bin"
  [ "$status" -eq 0 ]
  [[ "$output" == *"RTK already installed"* ]]
  [ ! -e "$BREW_LOG" ]
  [ ! -e "$MISE_LOG" ]
}

@test "Homebrew is used when it is available, even with mise present" {
  run _run_install "$(_path_with brew mise)"
  [ "$status" -eq 0 ]
  [[ "$(cat "$BREW_LOG")" == "install rtk" ]]
  [ ! -e "$MISE_LOG" ]
}

@test "without Homebrew, mise installs the tool globally" {
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$(cat "$MISE_LOG")" == "use -g rtk" ]]
  [[ "$output" == *"RTK installed"* ]]
}

@test "a mise install resolves in the same shell through the shims dir" {
  # The steps after the install (the rtk hook, the worktrunk plugin) look the
  # tool up with command -v. Without the shims on PATH they would skip it until
  # the next login.
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"RESOLVED=$MISE_DATA_DIR/shims/rtk"* ]]
}

@test "a mise install that leaves no shim warns and fails" {
  # mise exits 0 but writes nothing to the shims dir: the later command -v
  # would fail with no hint why.
  cat > "$STUBS/mise" <<'EOF2'
#!/usr/bin/env bash
echo "$*" >> "$MISE_LOG"
EOF2
  chmod +x "$STUBS/mise"
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not on PATH"* ]]
  [[ "$output" != *"RTK installed"* ]]
}

@test "a brew install that leaves no binary on PATH warns and fails" {
  export BREW_LEAVES_NO_BINARY=1
  run _run_install "$(_path_with brew)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not on PATH"* ]]
  [[ "$output" != *"RTK installed"* ]]
}

@test "with neither installer, the warning names both commands" {
  run _run_install "$(_path_with)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"brew install rtk"* ]]
  [[ "$output" == *"mise use -g rtk"* ]]
  [ ! -e "$BREW_LOG" ]
  [ ! -e "$MISE_LOG" ]
}

@test "a failed brew install is not reported as an install" {
  export BREW_INSTALL_FAILS=1
  run _run_install "$(_path_with brew)"
  [ "$status" -eq 1 ]
  [[ "$output" != *"RTK installed"* ]]
  [[ "$output" == *"brew install rtk"* ]]
}

@test "a failed mise install is not reported as an install" {
  export MISE_INSTALL_FAILS=1
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 1 ]
  [[ "$output" != *"RTK installed"* ]]
  [[ "$output" == *"mise use -g rtk"* ]]
}

# ─── Callers ─────────────────────────────────────────────────────────────────
# The callers pass their own formula and mise tool names; a swapped or misspelt
# name installs nothing on the machines that need the fallback, so the wiring
# is pinned here with the helper stubbed to record its arguments.

# _run_caller STEPS_FILE FN — runs FN from STEPS_FILE with the ui helpers and
# install_brew_or_mise stubbed. `wt` and `rtk` are absent from PATH so each step
# reaches its install.
_run_caller() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    install_brew_or_mise() { echo "HELPER $*"; return "${HELPER_STATUS:-1}"; }
    . "$1"
    PATH="${CALLER_PATH:-/usr/bin:/bin}"
    "$2"
  ' _ "$1" "$2"
}

@test "step_install_rtk installs rtk through the brew-or-mise helper" {
  run _run_caller "$REPO_ROOT/ai/rtk/steps.sh" step_install_rtk
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER rtk rtk rtk rtk"* ]]
}

@test "the worktrunk plugin step installs worktrunk through the helper first" {
  run _run_caller "$REPO_ROOT/ai/claude/steps.sh" step_claude_worktrunk_plugin
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER wt worktrunk worktrunk worktrunk"* ]]
}

@test "the worktrunk plugin step goes on to the plugin list once worktrunk installs" {
  cat > "$STUBS/wt" <<'EOF2'
#!/usr/bin/env bash
echo "WT $*"
[[ "$*" == "config plugins list" ]] && echo "claude"
exit 0
EOF2
  chmod +x "$STUBS/wt"
  HELPER_STATUS=0 CALLER_PATH="$STUBS:/usr/bin:/bin" run _run_caller "$REPO_ROOT/ai/claude/steps.sh" step_claude_worktrunk_plugin
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrunk Claude plugin already installed"* ]]
  [[ "$output" != *"skipped"* ]]
}
