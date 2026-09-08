#!/usr/bin/env bats
# Tests for bin/local/select-tests — change-based bats test selection.
#
# The script is sourced so its internal functions can be tested directly.
# The dependency graph is exercised against the real codebase to verify
# the transitive closure catches indirect dependencies. Because these
# assertions rely on how real files currently source each other, a
# refactor of one of those sourcing relationships (e.g. lib/conventions.sh
# -> bin/local/check-surface-compat) can fail a test here for a reason
# unrelated to the change under review — check the sourcing chain before
# assuming the dependency-graph logic itself regressed.

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../bin/local/select-tests
  source "$REPO_ROOT/bin/local/select-tests"
}

teardown() {
  common_teardown
}

# ── Source edge extraction ───────────────────────────────────────────────────

@test "source edges: lib/constants.sh sources lib/roots.sh via dirname" {
  run _source_edges_for lib/constants.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/roots.sh"* ]]
}

@test "source edges: bin/otto-workbench sources lib/ui.sh" {
  run _source_edges_for bin/otto-workbench
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/ui.sh"* ]]
}

@test "source edges: lib/migrations.sh sources lib/components.sh via LIB_SRC_DIR" {
  run _source_edges_for lib/migrations.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/components.sh"* ]]
}

@test "source edges: lib/ui.sh does not list itself" {
  run _source_edges_for lib/ui.sh
  [ "$status" -eq 0 ]
  # The self-reference in comments must be filtered out
  local lines
  lines=$(echo "$output" | grep -c '^lib/ui\.sh$' || true)
  [ "$lines" -eq 0 ]
}

@test "source edges: sibling sourcing via _lib_dir vars" {
  run _source_edges_for lib/setup.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/output.sh"* ]]
  [[ "$output" == *"lib/prompts.sh"* ]]
}

# ── Reverse-transitive closure ───────────────────────────────────────────────

@test "reverse closure: lib/roots.sh reaches lib/constants.sh" {
  _build_dep_graph
  run _reverse_closure "lib/roots.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/constants.sh"* ]]
}

@test "reverse closure: lib/roots.sh reaches lib/registries.sh" {
  _build_dep_graph
  run _reverse_closure "lib/roots.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/registries.sh"* ]]
}

@test "reverse closure: lib/components.sh reaches bin/otto-workbench" {
  _build_dep_graph
  run _reverse_closure "lib/components.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin/otto-workbench"* ]]
}

@test "reverse closure: lib/conventions.sh reaches bin/local/check-surface-compat" {
  _build_dep_graph
  run _reverse_closure "lib/conventions.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin/local/check-surface-compat"* ]]
}

@test "reverse closure: a file with no dependents returns only itself" {
  _build_dep_graph
  run _reverse_closure "bin/otto-workbench"
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 1 ]
  [[ "$output" == *"bin/otto-workbench"* ]]
}

# ── Change expansion ────────────────────────────────────────────────────────

@test "expand: lib/roots.sh pulls in transitive dependents" {
  local expanded
  expanded=$(_expand_changed_files "lib/roots.sh")
  [[ "$expanded" == *"lib/roots.sh"* ]]
  [[ "$expanded" == *"lib/constants.sh"* ]]
  [[ "$expanded" == *"lib/registries.sh"* ]]
}

@test "expand: a non-library file passes through unchanged" {
  local expanded
  expanded=$(_expand_changed_files "tests/run_tests.bats")
  # Only the file itself — no graph expansion
  [ "$(echo "$expanded" | wc -l | tr -d ' ')" -eq 1 ]
  [[ "$expanded" == *"tests/run_tests.bats"* ]]
}

@test "expand: multiple files merge their closures" {
  local expanded
  expanded=$(_expand_changed_files "lib/conventions.sh" "lib/components.sh")
  # lib/conventions.sh -> bin/local/check-surface-compat
  [[ "$expanded" == *"bin/local/check-surface-compat"* ]]
  # lib/components.sh -> lib/migrations.sh -> bin/otto-workbench
  [[ "$expanded" == *"bin/otto-workbench"* ]]
}

# ── Path matching ────────────────────────────────────────────────────────────

@test "path_matches: exact file match" {
  run _path_matches "lib/ui.sh" "lib/ui.sh"
  [ "$status" -eq 0 ]
}

@test "path_matches: directory containment" {
  run _path_matches "lib/ai/core.sh" "lib/ai"
  [ "$status" -eq 0 ]
}

@test "path_matches: no match across directories" {
  run _path_matches "bin/otto-workbench" "lib/ui.sh"
  [ "$status" -ne 0 ]
}

# ── Full-suite triggers ─────────────────────────────────────────────────────

@test "workflow changes trigger full suite" {
  run _is_full_suite_trigger ".github/workflows/ci.yml"
  [ "$status" -eq 0 ]
}

@test "test_helper.bash triggers full suite" {
  run _is_full_suite_trigger "tests/test_helper.bash"
  [ "$status" -eq 0 ]
}

@test "a regular source file does not trigger full suite" {
  run _is_full_suite_trigger "lib/roots.sh"
  [ "$status" -ne 0 ]
}
