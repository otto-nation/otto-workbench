#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # Create a bare remote with one commit on main — tests clone and branch from it.
  SHARED_REMOTE="$BATS_FILE_TMPDIR/remote"
  local tmp_local="$BATS_FILE_TMPDIR/seed"
  export GIT_CONFIG_GLOBAL=/dev/null
  git init --bare "$SHARED_REMOTE" --quiet --initial-branch=main
  git clone "$SHARED_REMOTE" "$tmp_local" --quiet 2>/dev/null
  cd "$tmp_local" || return 1
  git config user.email "test@example.com"
  git config user.name "Test"
  echo "init" > README.md
  git add .
  git commit -m "initial" --quiet
  git push --quiet
  cd /
  rm -rf "$tmp_local"
  export SHARED_REMOTE
}

setup() {
  load 'test_helper'
  source_lib

  TMPDIR="$(mktemp -d)"
  LOCAL_DIR="$TMPDIR/local"
  REMOTE_DIR="$TMPDIR/remote"

  # Copy shared remote per-test so pushes don't interfere across tests
  cp -R "$SHARED_REMOTE" "$REMOTE_DIR"
  export GIT_CONFIG_GLOBAL=/dev/null
  cd /
  git clone "$REMOTE_DIR" "$LOCAL_DIR" --quiet 2>/dev/null
  cd "$LOCAL_DIR" || return 1
  git config user.email "test@example.com"
  git config user.name "Test"

  # Create local feature branch (unpushed) — matches original make_git_remote behavior
  git checkout -b "feature/test" --quiet
  echo "feature" > feature.txt
  git add .
  git commit -m "feat: add feature" --quiet
}

teardown() {
  cd /
  rm -rf "$TMPDIR"
}

@test "pushes new branch to remote" {
  run push_branch "feature/test"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pushing new branch"* ]]
}

@test "reports up to date when already pushed" {
  git push --quiet origin feature/test
  git branch --set-upstream-to=origin/feature/test feature/test --quiet

  run push_branch "feature/test"
  [ "$status" -eq 0 ]
  [[ "$output" == *"up to date"* ]]
}

@test "pushes when local is ahead of remote" {
  git push --quiet origin feature/test
  git branch --set-upstream-to=origin/feature/test feature/test --quiet

  echo "more" > more.txt
  git add .
  git commit -m "feat: add more" --quiet

  run push_branch "feature/test"
  [ "$status" -eq 0 ]
  [[ "$output" == *"unpushed commits"* ]]
}

@test "fails when remote is ahead of local" {
  git push --quiet origin feature/test
  git branch --set-upstream-to=origin/feature/test feature/test --quiet

  # Simulate another contributor pushing directly to the remote branch
  OTHER_DIR="$TMPDIR/other"
  git clone "$REMOTE_DIR" "$OTHER_DIR" --quiet 2>/dev/null
  cd "$OTHER_DIR"
  git config user.email "other@example.com"
  git config user.name "Other"
  git checkout feature/test --quiet
  echo "remote only" > remote.txt
  git add .
  git commit -m "feat: remote commit" --quiet
  git push --quiet
  cd "$LOCAL_DIR"

  # Fetch so local knows about the remote change
  git fetch --quiet

  run push_branch "feature/test"
  [ "$status" -eq 1 ]
  [[ "$output" == *"pull first"* ]]
}

@test "fails when branches have diverged" {
  git push --quiet origin feature/test
  git branch --set-upstream-to=origin/feature/test feature/test --quiet

  # Remote gets a commit
  OTHER_DIR="$TMPDIR/other"
  git clone "$REMOTE_DIR" "$OTHER_DIR" --quiet 2>/dev/null
  cd "$OTHER_DIR"
  git config user.email "other@example.com"
  git config user.name "Other"
  git checkout feature/test --quiet
  echo "remote" > remote.txt
  git add .
  git commit -m "feat: remote" --quiet
  git push --quiet
  cd "$LOCAL_DIR"

  # Local also gets a different commit (diverged)
  echo "local" > local.txt
  git add .
  git commit -m "feat: local" --quiet

  git fetch --quiet

  run push_branch "feature/test"
  [ "$status" -eq 1 ]
  [[ "$output" == *"diverged"* ]]
}
