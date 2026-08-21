#!/usr/bin/env bats
# Tests for collect_component_registries — the component registry.yml discovery
# that lib/registries.sh's collect_registries and the public surface generator
# both build on. The depth these globs reach is the thing the two used to spell
# out separately, so it is asserted here rather than in either caller.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  rm -rf "$TMPDIR"
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
