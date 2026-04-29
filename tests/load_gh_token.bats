#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_HOME="$HOME"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  # Re-derive TASKFILE_ENV for the test HOME (constants.sh resolves at source time)
  TASKFILE_ENV="$HOME/.config/task/taskfile.env"
  # Ensure GH_TOKEN is not inherited from the test runner environment
  unset GH_TOKEN
  cd "$TMPDIR"
}

teardown() {
  export HOME="$ORIG_HOME"
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  unset GH_TOKEN
  common_teardown
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

# ── Org detection ────────────────────────────────────────────────────────────

@test "_detect_gh_org extracts org from SSH remote" {
  make_git_repo_with_org "$TMPDIR/repo" "otto-nation" "workbench"
  cd "$TMPDIR/repo"
  run _detect_gh_org
  [ "$status" -eq 0 ]
  [ "$output" = "otto-nation" ]
}

@test "_detect_gh_org extracts org from HTTPS remote" {
  mkdir -p "$TMPDIR/repo"
  git -C "$TMPDIR/repo" init --quiet
  git -C "$TMPDIR/repo" remote add origin "https://github.com/my-corp/my-repo.git"
  cd "$TMPDIR/repo"
  run _detect_gh_org
  [ "$status" -eq 0 ]
  [ "$output" = "my-corp" ]
}

@test "_detect_gh_org returns empty when no git repo" {
  mkdir -p "$TMPDIR/not-a-repo"
  cd "$TMPDIR/not-a-repo"
  run _detect_gh_org
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
}

@test "_detect_gh_org returns empty when no origin remote" {
  mkdir -p "$TMPDIR/repo"
  git -C "$TMPDIR/repo" init --quiet
  cd "$TMPDIR/repo"
  run _detect_gh_org
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
}

# ── Org normalization ────────────────────────────────────────────────────────

@test "_normalize_org_to_env uppercases and replaces hyphens" {
  run _normalize_org_to_env "otto-nation"
  [ "$output" = "OTTO_NATION" ]
}

@test "_normalize_org_to_env handles already-uppercase org" {
  run _normalize_org_to_env "ACME"
  [ "$output" = "ACME" ]
}

# ── Org-specific token resolution ────────────────────────────────────────────

@test "uses org-specific GH_TOKEN__<ORG> from env file" {
  make_git_repo_with_org "$TMPDIR/repo" "otto-nation" "workbench"
  cd "$TMPDIR/repo"
  mkdir -p "$TMPDIR/.config/task"
  printf 'GH_TOKEN=github_pat_default\nGH_TOKEN__OTTO_NATION=github_pat_org\n' > "$TMPDIR/.config/task/taskfile.env"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_org" ]
}

@test "falls back to default GH_TOKEN when no org-specific token" {
  make_git_repo_with_org "$TMPDIR/repo" "unknown-org" "some-repo"
  cd "$TMPDIR/repo"
  make_gh_token_config "$TMPDIR" "github_pat_default"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_default" ]
}

@test "org-specific token takes precedence over default GH_TOKEN" {
  make_git_repo_with_org "$TMPDIR/repo" "my-corp" "app"
  cd "$TMPDIR/repo"
  mkdir -p "$TMPDIR/.config/task"
  printf 'GH_TOKEN=github_pat_default\nGH_TOKEN__MY_CORP=github_pat_corp\n' > "$TMPDIR/.config/task/taskfile.env"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_corp" ]
}

@test "local .taskfile/taskfile.env GH_TOKEN wins over org-specific global" {
  make_git_repo_with_org "$TMPDIR/repo" "otto-nation" "workbench"
  cd "$TMPDIR/repo"
  mkdir -p "$TMPDIR/.config/task"
  printf 'GH_TOKEN__OTTO_NATION=github_pat_org\n' > "$TMPDIR/.config/task/taskfile.env"
  mkdir -p "$TMPDIR/repo/.taskfile"
  echo "GH_TOKEN=github_pat_local" > "$TMPDIR/repo/.taskfile/taskfile.env"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_local" ]
}

@test "works when not in a git repo — falls back to default GH_TOKEN" {
  mkdir -p "$TMPDIR/not-a-repo"
  cd "$TMPDIR/not-a-repo"
  make_gh_token_config "$TMPDIR" "github_pat_default"
  load_gh_token
  [ "$GH_TOKEN" = "github_pat_default" ]
}

@test "failure message includes org-specific variable name" {
  make_git_repo_with_org "$TMPDIR/repo" "otto-nation" "workbench"
  cd "$TMPDIR/repo"
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"GH_TOKEN__OTTO_NATION"* ]]
  [[ "$output" == *"otto-nation"* ]]
}
