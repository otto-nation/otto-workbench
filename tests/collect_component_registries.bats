#!/usr/bin/env bats
# Tests for collect_component_registries — the component registry.yml discovery
# that lib/registries.sh's collect_registries and the public surface generator
# both build on. The depth these globs reach is the thing the two used to spell
# out separately, so it is asserted here rather than in either caller.

setup() {
  load 'test_helper'
  common_setup

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  common_teardown
}

_write_registry() {
  local dir="$1"
  mkdir -p "$dir"
  printf 'meta:\n  section: Test\n  validation: none\ntools: []\n' > "$dir/registry.yml"
}

@test "finds a registry one directory below the root" {
  _write_registry "$TMPDIR/comp"

  local -a found=()
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 1 ]]
  [[ "${found[0]}" == "$TMPDIR/comp/registry.yml" ]]
}

@test "finds a registry two directories below the root" {
  _write_registry "$TMPDIR/ai/claude"

  local -a found=()
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 1 ]]
  [[ "${found[0]}" == "$TMPDIR/ai/claude/registry.yml" ]]
}

@test "a registry three directories down is out of reach" {
  _write_registry "$TMPDIR/a/b/c"

  local -a found=()
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 0 ]]
}

@test "a root with no registries yields an empty array, not the glob patterns" {
  local -a found=()
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 0 ]]
}

@test "a non-registry yaml beside a registry is not collected" {
  _write_registry "$TMPDIR/comp"
  printf 'env: []\n' > "$TMPDIR/comp/tool.env.yml"

  local -a found=()
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 1 ]]
  [[ "${found[0]}" == "$TMPDIR/comp/registry.yml" ]]
}

@test "the out array is replaced, not appended to" {
  _write_registry "$TMPDIR/comp"

  local -a found=("stale-entry")
  collect_component_registries found "$TMPDIR"
  [[ "${#found[@]}" -eq 1 ]]
  [[ "${found[0]}" == "$TMPDIR/comp/registry.yml" ]]
}

@test "collect_registries returns every component registry the primitive finds" {
  _write_registry "$TMPDIR/comp"
  _write_registry "$TMPDIR/ai/claude"

  local -a components=() all=()
  collect_component_registries components "$TMPDIR"
  collect_registries all "$TMPDIR"
  [[ "${#components[@]}" -eq 2 ]]

  printf '%s\n' "${components[@]}" | sort > "$BATS_TEST_TMPDIR/components.list"
  printf '%s\n' "${all[@]}" | sort > "$BATS_TEST_TMPDIR/all.list"

  # Lines in the primitive's output that collect_registries did not carry through.
  run comm -23 "$BATS_TEST_TMPDIR/components.list" "$BATS_TEST_TMPDIR/all.list"
  [ -z "$output" ]
}

@test "an env file in gitignored scratch is not collected" {
  # `ignore/` holds scratch, and a copy of the repo left there presents a second
  # copy of every *.env.yml. Discovery that walked into it reported each var as
  # declared by two registries and failed the push over a file that is not part
  # of the repo.
  _write_registry "$TMPDIR/comp"
  printf 'env: []\n' > "$TMPDIR/real.env.yml"
  mkdir -p "$TMPDIR/ignore/probe/zsh"
  printf 'env: []\n' > "$TMPDIR/ignore/probe/zsh/scratch.env.yml"

  local -a all=()
  collect_registries all "$TMPDIR"

  printf '%s\n' "${all[@]}" > "$BATS_TEST_TMPDIR/all.list"
  grep -q 'real.env.yml' "$BATS_TEST_TMPDIR/all.list"
  ! grep -q 'scratch.env.yml' "$BATS_TEST_TMPDIR/all.list"
}

@test "a scratch checkout's nested registry.yml is out of the walk" {
  # The component globs are depth-bounded and so cannot reach into `ignore/`
  # on their own. Asserted anyway: a future widening of that depth would
  # otherwise reopen the duplicate-registry failure silently.
  _write_registry "$TMPDIR/comp"
  _write_registry "$TMPDIR/ignore/probe/ai"

  local -a all=()
  collect_registries all "$TMPDIR"

  printf '%s\n' "${all[@]}" > "$BATS_TEST_TMPDIR/all.list"
  ! grep -q '/ignore/' "$BATS_TEST_TMPDIR/all.list"
}
