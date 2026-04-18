#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # Create the bare remote once — tests clone from it in setup().
  SHARED_REMOTE="$BATS_FILE_TMPDIR/remote"
  local tmp_local="$BATS_FILE_TMPDIR/seed"
  make_git_remote "$SHARED_REMOTE" "$tmp_local" "feature/test"
  # Push feature branch so clones can check it out
  git push --quiet origin "feature/test"
  cd /
  rm -rf "$tmp_local"
  export SHARED_REMOTE
}

setup() {
  load 'test_helper'

  TMPDIR="$(mktemp -d)"
  LOCAL_DIR="$TMPDIR/local"
  FAKE_HOME="$TMPDIR/fakehome"

  clone_from_shared_remote "$SHARED_REMOTE" "$LOCAL_DIR" "feature/test"

  # Resolve symlinks so comparisons work on macOS (/tmp -> /private/tmp)
  LOCAL_DIR="$(cd "$LOCAL_DIR" && pwd -P)"

  # Use a writable fake HOME so wt_ensure_gitignore can write global config
  mkdir -p "$FAKE_HOME"
  export HOME="$FAKE_HOME"
  unset GIT_CONFIG_GLOBAL

  # Source worktree lib
  source "$REPO_ROOT/lib/worktree.sh"
}

teardown() {
  cd /
  rm -rf "$TMPDIR"
}

# ─── wt_detect_username ──────────────────────────────────────────────────────

@test "wt_detect_username uses git user.name" {
  git config user.name "Isaac"
  wt_detect_username
  [ "$WT_USERNAME" = "isaac" ]
}

@test "wt_detect_username lowercases and takes first name" {
  git config user.name "Isaac Garcia"
  wt_detect_username
  [ "$WT_USERNAME" = "isaac" ]
}

# ─── wt_build_branch ─────────────────────────────────────────────────────────

@test "wt_build_branch adds feat/ prefix for plain names" {
  git config user.name "Isaac"
  wt_build_branch "add-search"
  [ "$WT_BRANCH" = "isaac/feat/add-search" ]
}

@test "wt_build_branch preserves slash structure" {
  git config user.name "Isaac"
  wt_build_branch "fix/race-condition"
  [ "$WT_BRANCH" = "isaac/fix/race-condition" ]
}

@test "wt_build_branch preserves issue prefix" {
  git config user.name "Isaac"
  wt_build_branch "PROJ-123/oauth-login"
  [ "$WT_BRANCH" = "isaac/PROJ-123/oauth-login" ]
}

# ─── wt_create ────────────────────────────────────────────────────────────────

@test "wt_create creates worktree directory" {
  wt_create "test-feature" "main"
  [ -d ".worktrees/test-feature" ]
}

@test "wt_create creates branch with correct name" {
  git config user.name "Test"
  wt_create "test-feature" "main"
  git show-ref --verify --quiet "refs/heads/test/feat/test-feature"
}

@test "wt_create fails if worktree already exists" {
  wt_create "test-feature" "main"
  run wt_create "test-feature" "main"
  [ "$status" -ne 0 ]
  [[ "$output" == *"already exists"* ]]
}

@test "wt_create worktree is a valid git working tree" {
  wt_create "test-feature" "main"
  [ -f ".worktrees/test-feature/README.md" ]
  git -C ".worktrees/test-feature" status >/dev/null 2>&1
}

# ─── wt_list ──────────────────────────────────────────────────────────────────

@test "wt_list shows created worktrees" {
  wt_create "feature-a" "main"
  wt_create "feature-b" "main"
  run wt_list
  [ "$status" -eq 0 ]
  [[ "$output" == *"feature-a"* ]]
  [[ "$output" == *"feature-b"* ]]
}

@test "wt_list reports no worktrees when none exist" {
  run wt_list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No worktrees"* ]]
}

# ─── wt_path ──────────────────────────────────────────────────────────────────

@test "wt_path prints absolute path" {
  wt_create "test-feature" "main"
  run wt_path "test-feature"
  [ "$status" -eq 0 ]
  [[ "$output" == *"/.worktrees/test-feature" ]]
  [ -d "$output" ]
}

@test "wt_path fails for nonexistent worktree" {
  run wt_path "nope"
  [ "$status" -ne 0 ]
}

# ─── wt_status ────────────────────────────────────────────────────────────────

@test "wt_status shows branch info for named worktree" {
  git config user.name "Test"
  wt_create "test-feature" "main"
  run wt_status "test-feature"
  [ "$status" -eq 0 ]
  [[ "$output" == *"test/feat/test-feature"* ]]
}

# ─── wt_remove ────────────────────────────────────────────────────────────────

@test "wt_remove cleans up worktree directory" {
  wt_create "test-feature" "main"
  [ -d ".worktrees/test-feature" ]
  wt_remove "test-feature"
  [ ! -d ".worktrees/test-feature" ]
}

@test "wt_remove fails on dirty worktree without --force" {
  wt_create "test-feature" "main"
  # Modify a tracked file so git diff --quiet detects the change
  echo "dirty" >> ".worktrees/test-feature/README.md"
  run wt_remove "test-feature"
  [ "$status" -ne 0 ]
  [[ "$output" == *"uncommitted changes"* ]]
}

@test "wt_remove --force removes dirty worktree" {
  wt_create "test-feature" "main"
  echo "dirty" > ".worktrees/test-feature/dirty.txt"
  wt_remove "test-feature" "--force"
  [ ! -d ".worktrees/test-feature" ]
}

@test "wt_remove fails for nonexistent worktree" {
  run wt_remove "nope"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found"* ]]
}

# ─── wt_repo_root ─────────────────────────────────────────────────────────────

@test "wt_repo_root resolves from main working tree" {
  wt_repo_root
  [ "$WT_REPO_ROOT" = "$LOCAL_DIR" ]
}

@test "wt_repo_root resolves from inside a worktree" {
  wt_create "test-feature" "main"
  cd ".worktrees/test-feature"
  wt_repo_root
  [ "$WT_REPO_ROOT" = "$LOCAL_DIR" ]
}

# ─── wt_ensure_gitignore ─────────────────────────────────────────────────────

@test "wt_ensure_gitignore creates ignore file with .worktrees" {
  wt_ensure_gitignore
  grep -qxF ".worktrees" "$FAKE_HOME/.config/git/ignore"
}

@test "wt_ensure_gitignore is idempotent" {
  wt_ensure_gitignore
  wt_ensure_gitignore
  local count
  count=$(grep -cxF ".worktrees" "$FAKE_HOME/.config/git/ignore")
  [ "$count" -eq 1 ]
}
