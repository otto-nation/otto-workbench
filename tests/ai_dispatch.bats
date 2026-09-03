#!/usr/bin/env bats
# Tests for the AI parent dispatcher — ai/steps.sh — and the work it does ahead
# of the selected sub-tools.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME/.local/state/workbench"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$FAKE_HOME/.local/state/workbench"
  GENERATED_RULES="$WORKBENCH_STATE_DIR/rules"
  RAN="$TMPDIR/ran"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _selection TOOL... — records TOOL... as the saved ai.tools selection, in the
# order given. That order is the one the operator typed at the setup menu.
_selection() {
  local yml="$FAKE_HOME/.local/state/workbench/install.yml"
  printf 'components:\n  ai:\n    tools:\n' > "$yml"
  local tool
  for tool in "$@"; do
    printf -- '      - %s\n' "$tool" >> "$yml"
  done
}

# _run_dispatch — sources the real dispatcher against the fake HOME, replaces
# every sync_<tool> with a stub that records its name, and dispatches.
#
# Each stub also records whether the shared rule layers were already refreshed
# when it ran, which is the ordering the dispatcher owes every harness.
_run_dispatch() {
  run bash -c '
    HOME="$2"
    # Named rather than read as $3 inside the stubs: a positional referenced in
    # a function body belongs to the function, not to the shell that runs it.
    ran="$3"
    generated="$4"
    . "$1/lib/ui.sh"
    . "$1/ai/steps.sh"
    _note() { [[ -f "$generated/workbench.md" ]] && echo "rules_ready" >> "$ran"; }
    sync_claude() { _note; echo sync_claude >> "$ran"; }
    sync_pi()     { _note; echo sync_pi     >> "$ran"; }
    sync_skills() { _note; echo sync_skills >> "$ran"; }
    sync_serena() { _note; echo sync_serena >> "$ran"; }
    sync_ai
  ' _ "$REPO_ROOT" "$FAKE_HOME" "$RAN" "$GENERATED_RULES"
}

@test "the shared rule layers are refreshed before any tool syncs" {
  # The bug this ordering replaces a tool ordering with: Pi's context file used
  # to be built out of Claude Code's installed rules, so the dispatcher had to
  # run claude first and a machine without it got no rules at all.
  _selection pi

  _run_dispatch
  [ "$status" -eq 0 ]
  run cat "$RAN"
  [ "${lines[0]}" = "rules_ready" ]
  [ "${lines[1]}" = "sync_pi" ]
}

@test "the shared rule layers are refreshed even with no tool selected" {
  _selection

  _run_dispatch
  [ "$status" -eq 0 ]
  [ -f "$GENERATED_RULES/workbench.md" ]
  [ ! -e "$RAN" ]
}

@test "tools dispatch in the order the operator selected them" {
  _selection serena pi claude skills

  _run_dispatch
  run grep -v rules_ready "$RAN"
  [ "${lines[0]}" = "sync_serena" ]
  [ "${lines[1]}" = "sync_pi" ]
  [ "${lines[2]}" = "sync_claude" ]
  [ "${lines[3]}" = "sync_skills" ]
}

@test "a tool the operator did not select is never dispatched" {
  _selection claude

  _run_dispatch
  run grep -v rules_ready "$RAN"
  [ "$output" = "sync_claude" ]
}

@test "setup refreshes the rule layers before it registers any tool's steps" {
  # The install-time half of the same contract, and the half with no seam to run:
  # ai/setup.sh performs its main flow at source time — installing bin scripts,
  # running steps, recording state — so the registration loop cannot be exercised
  # on its own without doing all of that to the machine. The contract is asserted
  # against the source instead.
  run awk '/workbench-rules" sync$/{ found=1 } /register_\$\{_tool\}_steps"$/{ print found; exit }' \
    "$REPO_ROOT/ai/setup.sh"
  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
}
