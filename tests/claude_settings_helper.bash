#!/usr/bin/env bash
# Helpers shared by the claude_settings_* suites.

# _claude_settings_setup_file — the setup_file every claude_settings_* suite
# shares: collects the registry permissions once into
# $BATS_FILE_TMPDIR/registry_perms.list, then runs one sync into a sandbox HOME
# at $BATS_FILE_TMPDIR/home.
_claude_settings_setup_file() {
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  # Run in its own bash, not this one — see _fresh_bash.
  _fresh_bash '
    . "$1/lib/registries.sh"
    perms=()
    collect_registry_permissions perms "$1"
    printf "%s\n" "${perms[@]}"
  ' "$repo_root" > "$BATS_FILE_TMPDIR/registry_perms.list"
  _sync_settings_into "$BATS_FILE_TMPDIR/home" "$repo_root"
}

# _fresh_bash SCRIPT [ARGS...] — runs SCRIPT under `set -e` in a new bash
# process rather than a subshell of the test. bats installs a DEBUG trap for its
# stack traces, and a subshell inherits it, so every simple command the code
# under test runs pays for one trap call. A registry scan runs tens of
# thousands of them: one settings sync measured ~25s in a subshell against
# ~3s in its own process. Exported variables reach it; functions do not.
_fresh_bash() {
  local script="$1"; shift
  bash -e -c "$script" _ "$@"
}

# _sync_settings_into FAKE_HOME REPO_ROOT — runs step_claude_settings against a
# sandbox HOME. constants.sh derives every path from HOME at source time, so
# presetting it keeps the real ~/.claude untouched.
#
# A root is derived from HOME only when nothing has set it yet — roots.sh
# prefers an existing value. Name every root the sandbox owns rather than
# trusting HOME to imply them, so a value inherited from the caller cannot point
# the manifest into the operator's own ~/.local/state. The exports stay in this
# shell because the suites' tests inherit them.
_sync_settings_into() {
  local fake_home="$1" repo_root="$2"
  mkdir -p "$fake_home"
  export HOME="$fake_home"
  export WORKBENCH_CONFIG_DIR="$fake_home/.config/workbench"
  export WORKBENCH_STATE_DIR="$fake_home/.local/state/workbench"
  export WORKBENCH_CACHE_DIR="$fake_home/.cache/workbench"
  export WORKBENCH_DIR="$repo_root"
  export WORKBENCH_STABLE_DIR="$repo_root"
  export NO_COLOR=1
  _fresh_bash '
    . "$1/lib/ui.sh"
    . "$1/ai/claude/steps.sh"
    step_claude_settings >/dev/null
  ' "$repo_root"
}

# _sync_run FAKE_HOME STATE_DIR — one step_claude_settings against a sandbox.
# In its own process because constants.sh freezes every path from HOME and the
# state root at source time: a second run needs a fresh process, not a second
# call. _fresh_bash, not a subshell, for speed — see _fresh_bash above. The
# step's warnings are what it prints, so they reach a caller using `run`.
_sync_run() {
  env HOME="$1" WORKBENCH_CONFIG_DIR="$1/.config/workbench" WORKBENCH_STATE_DIR="$2" \
    WORKBENCH_CACHE_DIR="$1/.cache/workbench" WORKBENCH_DIR="$REPO_ROOT" \
    WORKBENCH_STABLE_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -e -c '
      mkdir -p "$HOME"
      . "$WORKBENCH_DIR/lib/ui.sh"
      . "$WORKBENCH_DIR/ai/claude/steps.sh"
      step_claude_settings' _
}

# Runs the Bash PreToolUse guard against a mock payload. Every Bash rule lives
# in that one script, so these tests exercise the source rather than a
# JSON-escaped copy of it. The payload's cwd is the test's own unless it names
# one, as Claude Code always sends it.
_run_guard() {
  _payload_with_cwd "$PWD" "$1" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" 2>&1
}
