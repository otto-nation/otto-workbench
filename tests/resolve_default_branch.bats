#!/usr/bin/env bats
# Coverage for resolve_default_branch in lib/ai/core.sh — the shared derivation
# used by load_pr_context (lib/ai/pr.sh) and the `review:` Taskfile task. `git
# rev-parse --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when
# it fails, which used to defeat a "${DEFAULT_BRANCH:-main}" fallback at both
# call sites. `git symbolic-ref` prints nothing on failure, so the fallback here
# actually fires.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  export GIT_CONFIG_GLOBAL=/dev/null
}

teardown() {
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_repo_no_default_branch DIR BRANCH — bare remote + clone with one commit,
# the way an unfetched clone or a `wt-init`-converted repo ends up: no
# refs/remotes/origin/HEAD symref, because the clone happened before the
# remote had any commit for HEAD to point at.
_make_repo_no_default_branch() {
  local dir="$1"
  local initial_branch="${2:-main}"
  git init --bare "$dir/remote.git" --quiet --initial-branch="$initial_branch"
  git clone "$dir/remote.git" "$dir/repo" --quiet 2>/dev/null
  git -C "$dir/repo" config core.hooksPath /dev/null
  git -C "$dir/repo" config user.email "test@example.com"
  git -C "$dir/repo" config user.name "Test"
  echo "init" > "$dir/repo/README.md"
  git -C "$dir/repo" add .
  git -C "$dir/repo" commit -m "initial" --quiet
  git -C "$dir/repo" push --quiet
}

@test "falls back to main when origin/HEAD is missing" {
  _make_repo_no_default_branch "$TMPDIR" "main"

  # Confirm the precondition this test relies on — no symref means the fix's
  # `git symbolic-ref` line (not `rev-parse --abbrev-ref`) is the one being exercised.
  ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "main" ]
}

@test "resolves origin/HEAD to a non-main default branch" {
  _make_repo_no_default_branch "$TMPDIR" "trunk"
  git -C "$TMPDIR/repo" fetch origin --quiet
  # Point origin/HEAD explicitly, the way `git remote set-head origin -a` would
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "trunk" ]
}
