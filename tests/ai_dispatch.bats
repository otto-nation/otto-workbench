#!/usr/bin/env bats
# Tests for the AI parent dispatcher — ai/steps.sh — and the order it runs the
# selected sub-tools in.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME/.local/state/workbench"
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
_run_dispatch() {
  run bash -c '
    HOME="$2"
    # Named rather than read as $3 inside the stubs: a positional referenced in
    # a function body belongs to the function, not to the shell that runs it.
    ran="$3"
    . "$1/lib/ui.sh"
    . "$1/ai/steps.sh"
    sync_claude() { echo sync_claude >> "$ran"; }
    sync_pi()     { echo sync_pi     >> "$ran"; }
    sync_skills() { echo sync_skills >> "$ran"; }
    sync_serena() { echo sync_serena >> "$ran"; }
    sync_ai
  ' _ "$REPO_ROOT" "$FAKE_HOME" "$RAN"
}

@test "claude syncs before pi even when the saved selection lists pi first" {
  # The dependency the ordering exists for: step_pi_guidelines builds Pi's
  # context file out of the rules step_claude_rules installs, so a selection
  # order that ran pi first left that file one sync behind every added rule.
  _selection pi claude skills serena

  _run_dispatch
  [ "$status" -eq 0 ]
  run cat "$RAN"
  [ "${lines[0]}" = "sync_claude" ]
  [ "${lines[1]}" = "sync_pi" ]
}

@test "a tool the ordering does not name keeps its place after the ones it does" {
  _selection serena pi claude skills

  _run_dispatch
  run cat "$RAN"
  [ "${lines[0]}" = "sync_claude" ]
  [ "${lines[1]}" = "sync_pi" ]
  [ "${lines[2]}" = "sync_serena" ]
  [ "${lines[3]}" = "sync_skills" ]
}

@test "a tool the operator did not select is never dispatched" {
  _selection claude

  _run_dispatch
  run cat "$RAN"
  [ "$output" = "sync_claude" ]
}

@test "an empty selection dispatches nothing and succeeds" {
  _selection

  _run_dispatch
  [ "$status" -eq 0 ]
  [ ! -e "$RAN" ]
}

@test "setup feeds its registration loop from the ordering, not the selection" {
  # The install-time half of the same contract, and the half with no seam to run:
  # ai/setup.sh performs its main flow at source time — installing bin scripts,
  # running steps, recording state — so the registration loop cannot be exercised
  # on its own without doing all of that to the machine. The contract is asserted
  # against the source instead. run_steps runs in registration order, so this loop
  # is where install-time ordering is decided, and a selection listing pi first
  # would otherwise build Pi's context file before Claude's rules exist at all.
  run awk '/register_\$\{_tool\}_steps"$/,/^done/' "$REPO_ROOT/ai/setup.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"ai_tool_order"* ]]
}

@test "ai_tool_order drops nothing it was given" {
  run bash -c '
    HOME="$2"
    . "$1/lib/ui.sh"
    . "$1/ai/steps.sh"
    ai_tool_order pi serena claude skills
  ' _ "$REPO_ROOT" "$FAKE_HOME"
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 4 ]
  [ "${lines[0]}" = "claude" ]
  [ "${lines[1]}" = "pi" ]
}
