#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "generator writes both package snapshots" {
  run "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/public-surface.json" ]
  [ -f "$TMPDIR/ai/claude/public-surface.json" ]
}

@test "root snapshot names the otto-workbench package" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/public-surface.json"
  [ "$output" = "otto-workbench" ]
}

@test "ai snapshot names the otto-ai-tools package" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/ai/claude/public-surface.json"
  [ "$output" = "otto-ai-tools" ]
}

@test "root snapshot carries commands, config keys, and components" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" == *"command:get-secret"* ]]
  [[ "$output" == *"config:reuse.level"* ]]
  [[ "$output" == *"config:reuse.level=ultra"* ]]
  [[ "$output" == *"component:brew"* ]]
}

@test "workbench-scoped tools are not public" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:validate-all"* ]]
}

@test "ai/claude tools land in the ai snapshot, not the root one" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/ai/claude/public-surface.json"
  [[ "$output" == *"command:claude-review"* ]]
  [[ "$output" == *"agent:debugger"* ]]
  [[ "$output" == *"skill:pr-comments"* ]]
  [[ "$output" == *"setting:hooks"* ]]
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:claude-review"* ]]
}

@test "entries are sorted and unique" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries == (.entries | sort | unique)' "$TMPDIR/public-surface.json"
  [ "$output" = "true" ]
}

@test "--check passes against the committed snapshots" {
  run "$REPO_ROOT/bin/local/generate-public-surface" --check --quiet
  [ "$status" -eq 0 ]
}
