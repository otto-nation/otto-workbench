#!/usr/bin/env bats
# Tests for how a component's tool selection is recorded: picks only add
# (state_add_to_list), removal is explicit (state_remove_from_list, reached from
# `otto-workbench install COMPONENT --remove NAME`), and the menus mark what is
# already installed.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  export STATE_YML="$TMPDIR/state/install.yml"
  mkdir -p "$TMPDIR/state"
}

teardown() {
  common_teardown
}

# _in_workbench SCRIPT — runs SCRIPT with lib/ui.sh and lib/install.sh loaded and
# install state pointed at this test's own file.
_in_workbench() {
  bash -c "
    set -e
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/install.sh'
    INSTALL_YML_FILE='$STATE_YML'
    $1
  "
}

_list() {
  yq '.components.ai.tools | (. // []) | join(" ")' "$STATE_YML"
}

# ─── state_add_to_list ───────────────────────────────────────────────────────

@test "picking a subset keeps the tools installed before it" {
  run _in_workbench 'state_set_list ai.tools herdr; state_add_to_list ai.tools claude pi'
  [ "$status" -eq 0 ]
  [ "$(_list)" = "herdr claude pi" ]
}

@test "re-picking an installed tool does not duplicate it or reorder the list" {
  run _in_workbench 'state_set_list ai.tools herdr claude; state_add_to_list ai.tools claude herdr pi'
  [ "$status" -eq 0 ]
  [ "$(_list)" = "herdr claude pi" ]
}

@test "the first pick on a machine with no state records exactly that pick" {
  run _in_workbench 'state_add_to_list ai.tools claude'
  [ "$status" -eq 0 ]
  [ "$(_list)" = "claude" ]
}

# ─── state_remove_from_list / state_list_contains ────────────────────────────

@test "removal drops only the named tools and keeps the rest in order" {
  run _in_workbench 'state_set_list ai.tools herdr claude pi; state_remove_from_list ai.tools claude'
  [ "$status" -eq 0 ]
  [ "$(_list)" = "herdr pi" ]
}

@test "state_list_contains matches whole entries, not substrings" {
  run _in_workbench 'state_set_list ai.tools pip; state_list_contains ai.tools pi'
  [ "$status" -eq 1 ]
}

# ─── install COMPONENT --remove NAME ─────────────────────────────────────────

@test "parse_install_flags collects every --remove and keeps the component target" {
  run _in_workbench 'parse_install_flags ai --remove herdr --remove pi; echo "${INSTALL_TARGETS[*]}|${INSTALL_REMOVE[*]}"'
  [ "$status" -eq 0 ]
  [ "${lines[-1]}" = "ai|herdr pi" ]
}

@test "--remove with no name fails with a diagnostic" {
  run _in_workbench 'parse_install_flags ai --remove'
  [ "$status" -eq 2 ]
  [[ "$output" == *"--remove requires a tool name"* ]]
}

@test "remove_selected_tools drops a selected tool" {
  run _in_workbench 'state_set_list ai.tools herdr claude; remove_selected_tools ai herdr'
  [ "$status" -eq 0 ]
  [ "$(_list)" = "claude" ]
}

@test "remove_selected_tools refuses a name not in the selection and changes nothing" {
  run _in_workbench 'state_set_list ai.tools herdr claude; remove_selected_tools ai claude zed'
  [ "$status" -eq 1 ]
  [[ "$output" == *"zed is not in ai's selection (selected: herdr claude)"* ]]
  [ "$(_list)" = "herdr claude" ]
}

# ─── menus ───────────────────────────────────────────────────────────────────

@test "select_subdirs --installed marks installed entries and names the removal command" {
  mkdir -p "$TMPDIR/editors/zed" "$TMPDIR/editors/sublime"
  touch "$TMPDIR/editors/zed/setup.sh" "$TMPDIR/editors/sublime/setup.sh"
  run _in_workbench "state_set_list editors.tools zed
    select_subdirs _sel '$TMPDIR/editors' 'Pick' --installed editors.tools --single <<< 1"
  [ "$status" -eq 0 ]
  [[ "$output" == *"zed (installed)"* ]]
  [[ "$output" != *"sublime (installed)"* ]]
  [[ "$output" == *"otto-workbench install editors --remove <name>"* ]]
}

# A component installer that writes its menu answer with state_set_list drops
# every tool the operator did not re-pick. Each one must record through
# state_add_to_list instead.
@test "no component installer replaces its recorded selection" {
  local f
  for f in ai/setup.sh editors/setup.sh terminals/setup.sh; do
    run grep -n 'state_set_list' "$REPO_ROOT/$f"
    [ "$status" -eq 1 ]
    run grep -c 'state_add_to_list "$_STATE_KEY"' "$REPO_ROOT/$f"
    [ "$output" = "1" ]
  done
}
