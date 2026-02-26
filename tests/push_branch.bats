#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib

  TMPDIR="$(mktemp -d)"
  REMOTE_DIR="$TMPDIR/remote"
  LOCAL_DIR="$TMPDIR/local"

  make_git_remote "$REMOTE_DIR" "$LOCAL_DIR" "feature/test"
  # make_git_remote cds into LOCAL_DIR; we stay there for all tests
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
