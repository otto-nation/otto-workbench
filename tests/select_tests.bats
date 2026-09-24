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

# _build_dep_graph walks every bash script in the repo and forks ~15 greps per
# file to find its source edges — ~3s, and eight cases below need it. It is
# memoised per process, but bats runs each test in its own subshell, so the
# walk was paid eight times.
#
# Built once here and serialised. `declare -p` round-trips the associative
# array exactly, so a restoring test holds the same graph the walk produced
# rather than a summary of it.
setup_file() {
  load 'test_helper'
  common_setup
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  # Sourced without a `WORKBENCH_DIR=... ` prefix: bash discards a temporary
  # assignment once the builtin returns, and the script's own assignment to
  # that same name goes with it — leaving WORKBENCH_DIR empty, the tree walk
  # finding nothing, and a graph of zero keys that every restore then reads as
  # "no dependents". The script derives the root from BASH_SOURCE itself.
  # shellcheck source=/dev/null
  source "$repo_root/bin/local/select-tests"
  _build_dep_graph
  # Rewritten to `declare -gA`. `declare -p` emits a plain `declare -A`, and
  # sourcing that inside a function declares a *local* that vanishes on return
  # — the restore would appear to work and leave the caller with an empty
  # graph. It is the same trap _build_dep_graph documents for its own
  # declaration, one step removed.
  declare -p _REVERSE_DEPS \
    | sed 's/^declare -A /declare -gA /' > "$BATS_FILE_TMPDIR/dep_graph"
}

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../bin/local/select-tests
  source "$REPO_ROOT/bin/local/select-tests"
}

# _restore_dep_graph — the graph setup_file built, in place of a rebuild.
#
# `declare -gA` first: the cached line is a plain `declare -A`, which inside a
# function creates a local that vanishes when it returns — the same trap
# _build_dep_graph documents for its own declaration. _DEP_GRAPH_BUILT is set
# so a later _build_dep_graph call is the no-op it already knows how to be.
_restore_dep_graph() {
  # shellcheck source=/dev/null
  source "$BATS_FILE_TMPDIR/dep_graph"
  _DEP_GRAPH_BUILT=1
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
  _restore_dep_graph
  run _reverse_closure "lib/roots.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/constants.sh"* ]]
}

@test "reverse closure: lib/roots.sh reaches lib/registries.sh" {
  _restore_dep_graph
  run _reverse_closure "lib/roots.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/registries.sh"* ]]
}

@test "reverse closure: lib/components.sh reaches bin/otto-workbench" {
  _restore_dep_graph
  run _reverse_closure "lib/components.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin/otto-workbench"* ]]
}

@test "reverse closure: lib/conventions.sh reaches bin/local/check-surface-compat" {
  _restore_dep_graph
  run _reverse_closure "lib/conventions.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin/local/check-surface-compat"* ]]
}

@test "reverse closure: a file with no dependents returns only itself" {
  _restore_dep_graph
  run _reverse_closure "bin/otto-workbench"
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 1 ]
  [[ "$output" == *"bin/otto-workbench"* ]]
}

# ── Change expansion ────────────────────────────────────────────────────────

@test "expand: lib/roots.sh pulls in transitive dependents" {
  local expanded
  _restore_dep_graph
  expanded=$(_expand_changed_files "lib/roots.sh")
  [[ "$expanded" == *"lib/roots.sh"* ]]
  [[ "$expanded" == *"lib/constants.sh"* ]]
  [[ "$expanded" == *"lib/registries.sh"* ]]
}

@test "expand: a non-library file passes through unchanged" {
  local expanded
  _restore_dep_graph
  expanded=$(_expand_changed_files "tests/run_tests.bats")
  # Only the file itself — no graph expansion
  [ "$(echo "$expanded" | wc -l | tr -d ' ')" -eq 1 ]
  [[ "$expanded" == *"tests/run_tests.bats"* ]]
}

@test "expand: multiple files merge their closures" {
  local expanded
  _restore_dep_graph
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
