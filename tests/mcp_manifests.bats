#!/usr/bin/env bats
# Validates all MCP manifest files in ai/claude/mcps/.
# Required fields: label (string), url (string), command (non-empty array).
# All tests are no-ops when no manifests exist.

setup() {
  load 'test_helper'
  common_setup
  MCP_DIR="$REPO_ROOT/ai/claude/mcps"
}

teardown() {
  common_teardown
}

@test "all MCP manifests are valid JSON" {
  for file in "$MCP_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    run jq empty "$file"
    echo "# $file"
    [ "$status" -eq 0 ]
  done
}

@test "all MCP manifests have a non-empty label" {
  for file in "$MCP_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    echo "# $file"
    run jq -e '.label | type == "string" and length > 0' "$file"
    [ "$status" -eq 0 ]
  done
}

@test "all MCP manifests have a non-empty url" {
  for file in "$MCP_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    echo "# $file"
    run jq -e '.url | type == "string" and length > 0' "$file"
    [ "$status" -eq 0 ]
  done
}

@test "all MCP manifests have a non-empty command array" {
  for file in "$MCP_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    echo "# $file"
    run jq -e '.command | type == "array" and length > 0' "$file"
    [ "$status" -eq 0 ]
  done
}

@test "all MCP manifest command entries are non-empty strings" {
  for file in "$MCP_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    echo "# $file"
    run jq -e '[.command[] | type == "string" and length > 0] | all' "$file"
    [ "$status" -eq 0 ]
  done
}
