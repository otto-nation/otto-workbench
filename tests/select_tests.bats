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
  # An empty graph is the failure mode both comments above describe, and it is
  # silent: every restore succeeds and every closure case reads "no dependents",
  # so five tests fail on their assertions rather than one fixture failing to
  # build. Checked here, where the cause is still visible.
  if [[ ${#_REVERSE_DEPS[@]} -eq 0 ]]; then
    echo "FATAL: _build_dep_graph produced no edges for $repo_root" >&2
    return 1
  fi

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

@test "source edges: bin/otto-workbench sources lib/maintenance.sh" {
  run _source_edges_for bin/otto-workbench
  [ "$status" -eq 0 ]
  [[ "$output" == *"lib/maintenance.sh"* ]]
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

@test "reverse closure: lib/maintenance.sh reaches bin/otto-workbench" {
  _restore_dep_graph
  run _reverse_closure "lib/maintenance.sh"
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
  run _path_matches "lib/ai/session-count.sh" "lib/ai"
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

# ── what counts as changed ───────────────────────────────────────────

# _scratch_repo — a one-file repo under this case's scratch, committed.
_scratch_repo() {
  local repo="$BATS_TEST_TMPDIR/repo"
  git init -q -b main "$repo"
  printf 'a\n' > "$repo/tracked.sh"
  git -C "$repo" add tracked.sh
  git -C "$repo" -c user.name=t -c user.email=t@t commit -q -m base
  printf '%s' "$repo"
}

@test "changed paths include an uncommitted edit and an untracked file" {
  local repo base
  repo="$(_scratch_repo)"
  base="$(git -C "$repo" rev-parse HEAD)"
  printf 'b\n' >> "$repo/tracked.sh"
  printf '@test "x" { true; }\n' > "$repo/new.bats"
  cd "$repo"
  run _changed_paths "$base"
  [ "$status" -eq 0 ]
  [[ "$output" == *"tracked.sh"* ]]
  [[ "$output" == *"new.bats"* ]]
}

@test "an unresolvable base yields no paths, so the caller runs everything" {
  local repo
  repo="$(_scratch_repo)"
  printf 'x\n' > "$repo/new.bats"
  cd "$repo"
  run _changed_paths refs/heads/nope
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ── validate: suites that scan the real tree ─────────────────────────────────
#
# A suite that hands the repo root to a collect_* scanner asserts over every
# registry or env file the scan finds, none of which it names as a path
# literal — so a change to one of them selects nothing unless the suite is
# always run. --validate refuses that shape outside ALWAYS_RUN_TESTS.

# _fake_suite NAME BODY — a one-file tests dir holding NAME.bats, with a path
# literal so the zero-refs check is not what answers.
#
# The collector calls below are assembled from $_COLLECT rather than written out:
# a literal call given a root spelling on one line of this file is exactly what
# --validate looks for, and would flag this suite as a real-tree scan.
_COLLECT=collect_registry_permissions
_fake_suite() {
  TESTS_DIR="$BATS_TEST_TMPDIR/tests"
  mkdir -p "$TESTS_DIR"
  printf 'source "$REPO_ROOT/lib/registries.sh"\n%s\n' "$2" > "$TESTS_DIR/$1.bats"
}

@test "validate: a suite scanning the real tree must be always run" {
  _fake_suite real_scan "  $_COLLECT perms \"\$repo_root\""
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  ALWAYS_RUN_TESTS=()
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  NO_REFS_TESTS=()
  run _validate_test_refs
  [ "$status" -eq 1 ]
  [[ "$output" == *"real_scan"* ]]
  [[ "$output" == *"ALWAYS_RUN_TESTS"* ]]
}

@test "validate: the same suite passes once it is always run" {
  _fake_suite real_scan "  $_COLLECT perms \"\$REPO_ROOT\""
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  ALWAYS_RUN_TESTS=(real_scan)
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  NO_REFS_TESTS=()
  run _validate_test_refs
  [ "$status" -eq 0 ]
}

@test "validate: a collector fed a fixture directory is not a real-tree scan" {
  _fake_suite fixture_scan "  $_COLLECT perms \"\$TMPDIR\""
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  ALWAYS_RUN_TESTS=()
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  NO_REFS_TESTS=()
  run _validate_test_refs
  [ "$status" -eq 0 ]
}

@test "validate: a commented-out real-tree scan is not counted" {
  _fake_suite commented "  # $_COLLECT perms \"\$REPO_ROOT\""
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  ALWAYS_RUN_TESTS=()
  # shellcheck disable=SC2034  # read by _validate_test_refs in the sourced select-tests
  NO_REFS_TESTS=()
  run _validate_test_refs
  [ "$status" -eq 0 ]
}

@test "validate: the real tests dir passes as a whole (zero-refs and real-tree checks)" {
  run _validate_test_refs
  [ "$status" -eq 0 ]
}
