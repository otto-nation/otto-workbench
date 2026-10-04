#!/usr/bin/env bash
# Helpers shared by the claude_settings_* suites.

# _sync_settings_into FAKE_HOME REPO_ROOT — runs step_claude_settings against a
# sandbox HOME. constants.sh derives every path from HOME at source time, so
# presetting it keeps the real ~/.claude untouched.
#
# A root is derived from HOME only when nothing has set it yet — roots.sh prefers
# an existing value, and whichever source runs first is the one that decides. The
# registries.sh above loads roots.sh under the real HOME, so WORKBENCH_STATE_DIR
# arrives here already pointing at the real state root and survives the HOME
# swap; step_claude_settings then writes its manifest into ~/.local/state. Name
# every root the sandbox owns rather than trusting HOME to imply them.
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
  # shellcheck source=/dev/null
  source "$repo_root/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$repo_root/ai/claude/steps.sh"
  step_claude_settings >/dev/null
}

# Runs the Bash PreToolUse guard against a mock payload. Every Bash rule lives
# in that one script, so these tests exercise the source rather than a
# JSON-escaped copy of it.
_run_guard() {
  echo "$1" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" 2>&1
}
