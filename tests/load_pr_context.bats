#!/usr/bin/env bats
# Coverage for load_pr_context's DEFAULT_BRANCH derivation in lib/ai/pr.sh —
# regression coverage for the origin/HEAD-missing bug. `git rev-parse
# --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when it fails,
# so the old derivation defeated its own "${DEFAULT_BRANCH:-main}" fallback.
# `git symbolic-ref` prints nothing on failure, so the fallback actually fires.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_HOME="$HOME"
  ORIG_PATH="$PATH"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  # Re-derive TASKFILE_ENV for the test HOME (constants.sh resolves at source time)
  # shellcheck disable=SC2034  # read by load_ai_command / load_gh_token
  TASKFILE_ENV="$HOME/.config/task/taskfile.env"
  unset GH_TOKEN
}

teardown() {
  export HOME="$ORIG_HOME"
  PATH="$ORIG_PATH"
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  unset GH_TOKEN
  common_teardown
}

# make_fake_gh — stub gh that succeeds on every subcommand. load_pr_context
# only uses gh to gate entry (repo access + auth); the DEFAULT_BRANCH
# derivation itself is pure git, so a blanket success stub is enough to reach it.
make_fake_gh() {
  mkdir -p "$TMPDIR/bin"
  cat > "$TMPDIR/bin/gh" << 'SCRIPT'
#!/bin/bash
exit 0
SCRIPT
  chmod +x "$TMPDIR/bin/gh"
  PATH="$TMPDIR/bin:$ORIG_PATH"
}

# _make_repo_no_default_branch DIR — bare remote + clone with one commit, the
# way an unfetched clone or a `wt-init`-converted repo ends up: no
# refs/remotes/origin/HEAD symref, because the clone happened before the
# remote had any branch to point HEAD at.
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
  git -C "$dir/repo" checkout -b feature/test --quiet
  echo "feature" > "$dir/repo/feature.txt"
  git -C "$dir/repo" add .
  git -C "$dir/repo" commit -m "feat: add feature" --quiet
}

@test "falls back to main when origin/HEAD is missing" {
  make_fake_gh
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  _make_repo_no_default_branch "$TMPDIR" "main"

  # Confirm the precondition this test relies on — no symref means the fix's
  # `git symbolic-ref` line (not `rev-parse --abbrev-ref`) is the one being exercised.
  ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null

  cd "$TMPDIR/repo"
  load_pr_context
  [ "$DEFAULT_BRANCH" = "main" ]
}

@test "resolves origin/HEAD to a non-main default branch" {
  make_fake_gh
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  _make_repo_no_default_branch "$TMPDIR" "trunk"
  # Point origin/HEAD explicitly, the way `git remote set-head origin -a` would
  git -C "$TMPDIR/repo" fetch origin --quiet
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  load_pr_context
  [ "$DEFAULT_BRANCH" = "trunk" ]
}
