#!/usr/bin/env bats
bats_require_minimum_version 1.5.0

setup_file() {
  load 'test_helper'
  SHARED_REMOTE="$BATS_FILE_TMPDIR/remote"
  local tmp_local="$BATS_FILE_TMPDIR/seed"
  make_git_remote "$SHARED_REMOTE" "$tmp_local" "isaac/fix/devhub_error_handling"

  cd "$tmp_local"
  git checkout main --quiet
  git checkout -b "isaac/feat/add_metrics" --quiet
  echo "metrics" > metrics.txt
  git add .
  git commit -m "feat: add metrics" --quiet

  git checkout -b "isaac/fix/cache_race" --quiet
  echo "cache" > cache.txt
  git add .
  git commit -m "fix: cache race" --quiet

  git checkout main --quiet
  git push "$SHARED_REMOTE" --all --quiet

  cd /
  rm -rf "$tmp_local"
  export SHARED_REMOTE
}

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1

  SCRIPT="$REPO_ROOT/bin/resolve-branch"

  TMPDIR="$(mktemp -d)"
  LOCAL_DIR="$TMPDIR/local"
  REMOTE_DIR="$TMPDIR/remote"

  cp -R "$SHARED_REMOTE" "$REMOTE_DIR"
  clone_from_shared_remote "$REMOTE_DIR" "$LOCAL_DIR" "main"
  git fetch origin --quiet
}

teardown() {
  cd /
  rm -rf "$TMPDIR"
  common_teardown
}

# ── Exact match ───────────────────────────────────────────────────────────────

@test "resolves exact local branch name" {
  git checkout isaac/fix/devhub_error_handling --quiet
  run "$SCRIPT" "isaac/fix/devhub_error_handling"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/fix/devhub_error_handling" ]]
}

@test "resolves exact remote-only branch name" {
  run "$SCRIPT" "isaac/feat/add_metrics"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/feat/add_metrics" ]]
}

# ── Worktree directory match ─────────────────────────────────────────────────

@test "resolves worktree directory basename to branch" {
  local wt_dir="$TMPDIR/isaac-fix-devhub_error_handling"
  git worktree add "$wt_dir" "isaac/fix/devhub_error_handling" --quiet

  run "$SCRIPT" "isaac-fix-devhub_error_handling"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/fix/devhub_error_handling" ]]

  git worktree remove "$wt_dir" --force 2>/dev/null || true
}

@test "resolves worktree directory during in-progress rebase (detached HEAD)" {
  local wt_dir="$TMPDIR/isaac-fix-devhub_error_handling"
  git worktree add "$wt_dir" "isaac/fix/devhub_error_handling" --quiet

  # Simulate rebase-in-progress: detach HEAD and create rebase-merge/head-name
  git -C "$wt_dir" checkout --detach --quiet
  local git_dir
  git_dir=$(git -C "$wt_dir" rev-parse --git-dir)
  [[ "$git_dir" = /* ]] || git_dir="$wt_dir/$git_dir"
  mkdir -p "$git_dir/rebase-merge"
  echo "refs/heads/isaac/fix/devhub_error_handling" > "$git_dir/rebase-merge/head-name"

  run "$SCRIPT" "isaac-fix-devhub_error_handling"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/fix/devhub_error_handling" ]]

  # Clean up rebase state before removing worktree
  rm -rf "$git_dir/rebase-merge"
  git -C "$wt_dir" checkout "isaac/fix/devhub_error_handling" --quiet 2>/dev/null || true
  git worktree remove "$wt_dir" --force 2>/dev/null || true
}

# ── Separator normalization ──────────────────────────────────────────────────

@test "resolves dash-separated input to slash-separated branch" {
  run "$SCRIPT" "isaac-fix-devhub_error_handling"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/fix/devhub_error_handling" ]]
}

@test "resolves dash-separated with different type segment" {
  run "$SCRIPT" "isaac-feat-add_metrics"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/feat/add_metrics" ]]
}

# ── Fuzzy match ──────────────────────────────────────────────────────────────

@test "resolves single fuzzy match" {
  run "$SCRIPT" "devhub_error"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "isaac/fix/devhub_error_handling" ]]
}

@test "fails with multiple fuzzy matches and lists candidates" {
  run "$SCRIPT" "isaac"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"Multiple branches"* ]]
  [[ "$output" == *"isaac/fix/devhub_error_handling"* ]]
  [[ "$output" == *"isaac/feat/add_metrics"* ]]
}

# ── Error cases ──────────────────────────────────────────────────────────────

@test "fails with no matching branch" {
  run "$SCRIPT" "nonexistent-branch-xyz"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"No branch found"* ]]
}

@test "fails with no argument" {
  run "$SCRIPT"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"Usage"* ]]
}
