#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib
  ORIG_HOME="$HOME"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Ensure GH_TOKEN is not inherited from the test runner environment
  unset GH_TOKEN
  cd "$TMPDIR"
}

teardown() {
  export HOME="$ORIG_HOME"
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  unset GH_TOKEN
}

# ── Failure cases ─────────────────────────────────────────────────────────────

@test "fails: no env file and GH_TOKEN not in environment" {
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"GH_TOKEN not configured"* ]]
}

@test "fails: env file exists but has no GH_TOKEN and not in environment" {
  mkdir -p "$TMPDIR/.config/task"
  echo "AI_COMMAND=some-ai" > "$TMPDIR/.config/task/taskfile.env"
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"GH_TOKEN not configured"* ]]
}

@test "fails: GH_TOKEN is commented out in env file and not in environment" {
  mkdir -p "$TMPDIR/.config/task"
  echo "# GH_TOKEN=github_pat_abc" > "$TMPDIR/.config/task/taskfile.env"
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"GH_TOKEN not configured"* ]]
}

@test "failure message includes path to env file" {
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *".config/task/taskfile.env"* ]]
}

@test "failure message includes PAT creation URL" {
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"github.com/settings/tokens"* ]]
}

# ── Success: from env file ────────────────────────────────────────────────────

@test "succeeds: GH_TOKEN set in global env file" {
  make_gh_token_config "$TMPDIR" "github_pat_test_token"
  run load_gh_token
  [ "$status" -eq 0 ]
}

@test "exports GH_TOKEN value from env file" {
  make_gh_token_config "$TMPDIR" "github_pat_from_file"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_from_file" ]
}

@test "reads GH_TOKEN with special characters in value" {
  mkdir -p "$TMPDIR/.config/task"
  echo "GH_TOKEN=github_pat_abc123_XYZ" > "$TMPDIR/.config/task/taskfile.env"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_abc123_XYZ" ]
}

# ── Success: env var fallback ─────────────────────────────────────────────────

@test "succeeds: falls back to GH_TOKEN already in environment" {
  export GH_TOKEN="github_pat_from_env"
  run load_gh_token
  [ "$status" -eq 0 ]
}

@test "env fallback succeeds even with no env file" {
  export GH_TOKEN="github_pat_from_env"
  run load_gh_token
  [ "$status" -eq 0 ]
}

# ── Precedence ────────────────────────────────────────────────────────────────

@test "env file GH_TOKEN takes precedence over environment GH_TOKEN" {
  export GH_TOKEN="github_pat_from_env"
  make_gh_token_config "$TMPDIR" "github_pat_from_file"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_from_file" ]
}

@test "prefers local .taskfile/taskfile.env over global for GH_TOKEN" {
  make_gh_token_config "$TMPDIR" "github_pat_global"
  mkdir -p "$TMPDIR/project/.taskfile"
  echo "GH_TOKEN=github_pat_local" > "$TMPDIR/project/.taskfile/taskfile.env"
  cd "$TMPDIR/project"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_local" ]
}
