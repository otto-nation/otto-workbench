#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  SHARED_REMOTE="$BATS_FILE_TMPDIR/remote"
  local tmp_local="$BATS_FILE_TMPDIR/seed"
  make_git_remote "$SHARED_REMOTE" "$tmp_local"
  # Push the feature branch so clone_from_shared_remote can check it out,
  # but tests start with it as a local-only branch (no upstream tracking).
  cd "$tmp_local" || return 1
  git push "$SHARED_REMOTE" feature/test --quiet
  cd /
  rm -rf "$tmp_local"
  export SHARED_REMOTE
}

setup() {
  load 'test_helper'
  common_setup
  source_lib

  TMPDIR="$(mktemp -d)"
  LOCAL_DIR="$TMPDIR/local"
  REMOTE_DIR="$TMPDIR/remote"

  cp -R "$SHARED_REMOTE" "$REMOTE_DIR"
  clone_from_shared_remote "$REMOTE_DIR" "$LOCAL_DIR"

  # Remove upstream tracking so tests start with an unpushed local branch
  git branch --unset-upstream feature/test 2>/dev/null || true
  git push origin --delete feature/test --quiet 2>/dev/null || true
}

teardown() {
  cd /
  rm -rf "$TMPDIR"
  common_teardown
}

# A remote that accepts a push and then rewinds the ref — a push that exits zero
# and lands nothing, which is the failure push_branch has to catch rather than
# report as a success.
lose_pushes() {
  cat > "$REMOTE_DIR/hooks/post-receive" <<'HOOK'
#!/usr/bin/env bash
while read -r old new ref; do
  if [ "$old" = "0000000000000000000000000000000000000000" ]; then
    git update-ref -d "$ref"
  else
    git update-ref "$ref" "$old"
  fi
done
HOOK
  chmod +x "$REMOTE_DIR/hooks/post-receive"
}

@test "reports a push that did not reach the remote" {
  lose_pushes
  run push_branch "feature/test"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the remote did not move"* ]]
}

@test "reports a lost push on a branch the remote already has" {
  git push --quiet origin feature/test
  git branch --set-upstream-to=origin/feature/test feature/test --quiet
  echo "more" > more.txt
  git add .
  git commit -m "feat: add more" --quiet

  lose_pushes
  run push_branch "feature/test"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the remote did not move"* ]]
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

@test "push.py runs under the shipped PYTHONPATH invocation" {
  # push.py's sibling imports (`from git import client`, `from core import log`)
  # resolve against ai/lib, not against ai/lib/git where the file lives — the
  # layer-package move deepened it by one directory, so the interpreter's
  # automatic sys.path[0] (the script's own directory) is no longer enough.
  # _push_verified's PYTHONPATH prefix is what closes that gap; --help exits
  # before argparse's required --branch check runs, so a bare rc=0 here is
  # entirely a claim about whether the import resolved.
  unset PYTHONPATH
  run _push_verified test-branch --help
  [ "$status" -eq 0 ]
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
