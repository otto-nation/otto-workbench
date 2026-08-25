#!/usr/bin/env bats
# Tests for ai/serena/bin/serena-mcp — which tree .mcp.json and .gitignore are
# written into.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  SERENA_MCP="$REPO_ROOT/ai/serena/bin/serena-mcp"
  TMPDIR="$(mktemp -d)"
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf 'x\n' > "$SEED/a.sh"
  make_container_seed "$SEED"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _run_in DIR ARGS... — serena-mcp with DIR as the working directory.
_run_in() {
  local dir="$1"
  shift
  cd "$dir" || return 1
  "$SERENA_MCP" "$@"
}

@test "init scaffolds the worktree, not the container it was run from" {
  make_worktree_container "$TMPDIR/c" "$SEED"

  run _run_in "$TMPDIR/c" init
  [ "$status" -eq 0 ]
  run jq -e '.mcpServers.serena' "$TMPDIR/c/main/.mcp.json"
  [ "$status" -eq 0 ]
  [ ! -e "$TMPDIR/c/.mcp.json" ]
  [ ! -e "$TMPDIR/c/.gitignore" ]
}

@test "init writes the gitignore entry into the worktree too" {
  make_worktree_container "$TMPDIR/c" "$SEED"

  run _run_in "$TMPDIR/c" init
  [ "$status" -eq 0 ]
  run grep -qxF '.serena/' "$TMPDIR/c/main/.gitignore"
  [ "$status" -eq 0 ]
}

@test "init from a subdirectory writes at the repo root" {
  mkdir -p "$SEED/deep/nested"

  run _run_in "$SEED/deep/nested" init
  [ "$status" -eq 0 ]
  [ -f "$SEED/.mcp.json" ]
  [ ! -e "$SEED/deep/nested/.mcp.json" ]
}

@test "init outside any repository still writes where it was run" {
  mkdir -p "$TMPDIR/loose"

  run _run_in "$TMPDIR/loose" init
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/loose/.mcp.json" ]
}

@test "init fails rather than write into a container with no worktree" {
  # A .mcp.json in a container is read by nothing and reachable by no .gitignore
  # rule, review, or CI check — the one case where falling through to the
  # current directory has to be an error instead.
  local container="$TMPDIR/c"
  make_empty_container "$container" "$SEED"

  run _run_in "$container" init
  [ "$status" -eq 1 ]
  [[ "$output" == *"No worktree resolved"* ]]
  [ ! -e "$container/.mcp.json" ]
}

@test "init is idempotent" {
  run _run_in "$SEED" init
  [ "$status" -eq 0 ]
  run _run_in "$SEED" init
  [ "$status" -eq 0 ]
  [[ "$output" == *"already configured"* ]]
  run grep -c '\.serena/' "$SEED/.gitignore"
  [ "$output" -eq 1 ]
}

@test "init preserves other servers already in .mcp.json" {
  printf '%s\n' '{"mcpServers":{"other":{"command":"x"}}}' > "$SEED/.mcp.json"

  run _run_in "$SEED" init
  [ "$status" -eq 0 ]
  run jq -e '.mcpServers.other and .mcpServers.serena' "$SEED/.mcp.json"
  [ "$status" -eq 0 ]
}

@test "status reads the worktree's file from inside the container" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _run_in "$TMPDIR/c" init

  run _run_in "$TMPDIR/c" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Serena is configured"* ]]
}

@test "status reports nothing configured before init" {
  run _run_in "$SEED" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"No"* ]]
}
