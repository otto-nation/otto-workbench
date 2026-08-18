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

@test "--check fails on a stale snapshot and never writes to it" {
  mkdir -p "$TMPDIR/ai/claude"
  cp "$REPO_ROOT/public-surface.json" "$TMPDIR/public-surface.json"
  cp "$REPO_ROOT/ai/claude/public-surface.json" "$TMPDIR/ai/claude/public-surface.json"
  jq '.entries += ["command:not-a-real-tool"]' "$TMPDIR/public-surface.json" > "$TMPDIR/mutated.json"
  mv "$TMPDIR/mutated.json" "$TMPDIR/public-surface.json"
  cp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"

  run "$REPO_ROOT/bin/local/generate-public-surface" --check --quiet --out-dir "$TMPDIR"
  [ "$status" -eq 1 ]

  run cmp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"
  [ "$status" -eq 0 ]
}

@test "generation is deterministic regardless of the caller's locale" {
  local dir_c="$TMPDIR/c" dir_en="$TMPDIR/en"
  env LC_ALL=C "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$dir_c" --quiet
  env LC_ALL=en_US.UTF-8 "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$dir_en" --quiet

  run cmp "$dir_c/public-surface.json" "$dir_en/public-surface.json"
  [ "$status" -eq 0 ]
  run cmp "$dir_c/ai/claude/public-surface.json" "$dir_en/ai/claude/public-surface.json"
  [ "$status" -eq 0 ]
}

@test "ai/serena tools land in the root snapshot, not ai/claude" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/public-surface.json"
  [ "$status" -eq 0 ]
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/ai/claude/public-surface.json"
  [ "$status" -eq 1 ]
}

@test "every ai/claude registry tool has a matching command entry" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  while IFS= read -r name; do
    run jq -e --arg e "command:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done < <(yq -r '.tools[].name' "$REPO_ROOT/ai/claude/registry.yml")
}

@test "every ai/claude agent has a matching agent entry" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  for f in "$REPO_ROOT"/ai/claude/agents/*.md; do
    name="$(basename "$f" .md)"
    run jq -e --arg e "agent:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done
}
