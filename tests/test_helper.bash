#!/usr/bin/env bash
# Shared setup helpers for workbench bats tests.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

# _assert_not_real_repo — fails hard if PWD is inside the real workbench repo.
_assert_not_real_repo() {
  local toplevel
  toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
  if [[ "$toplevel" == "$REPO_ROOT" ]]; then
    echo "FATAL: test is operating inside the real repo ($PWD)"
    echo "  REPO_ROOT=$REPO_ROOT"
    echo "  git toplevel=$toplevel"
    return 1
  fi
}

# common_setup — call first in every test's setup().
# Snapshots real repo state so common_teardown can detect contamination.
common_setup() {
  _REPO_CONFIG_SNAPSHOT="$(git -C "$REPO_ROOT" config --local --list 2>/dev/null | sort)"
  _REPO_HEAD_SNAPSHOT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
  _REPO_BRANCHES_SNAPSHOT="$(git -C "$REPO_ROOT" branch --list 2>/dev/null | sort)"
}

# common_teardown — call last in every test's teardown().
# Fails the test if anything in the real repo changed.
common_teardown() {
  local current_config current_head current_branches
  current_config="$(git -C "$REPO_ROOT" config --local --list 2>/dev/null | sort)"
  current_head="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
  current_branches="$(git -C "$REPO_ROOT" branch --list 2>/dev/null | sort)"

  if [[ "$current_config" != "$_REPO_CONFIG_SNAPSHOT" ]]; then
    echo "SAFETY: test contaminated real repo git config"
    diff <(echo "$_REPO_CONFIG_SNAPSHOT") <(echo "$current_config") || true
    return 1
  fi
  if [[ "$current_head" != "$_REPO_HEAD_SNAPSHOT" ]]; then
    echo "SAFETY: test moved real repo HEAD from $_REPO_HEAD_SNAPSHOT to $current_head"
    return 1
  fi
  if [[ "$current_branches" != "$_REPO_BRANCHES_SNAPSHOT" ]]; then
    echo "SAFETY: test modified real repo branches"
    diff <(echo "$_REPO_BRANCHES_SNAPSHOT") <(echo "$current_branches") || true
    return 1
  fi
}

# source_lib — loads all lib/ai/*.sh files into the current test context.
source_lib() {
  local f
  for f in "$REPO_ROOT/lib/ai/"*.sh; do
    # shellcheck disable=SC1090
    source "$f"
  done
}

# make_ai_config DIR COMMAND — writes a taskfile.env with AI_COMMAND=COMMAND.
make_ai_config() {
  local dir="$1"
  local command="$2"
  mkdir -p "$dir/.config/task"
  echo "AI_COMMAND=$command" > "$dir/.config/task/taskfile.env"
}

# make_gh_token_config DIR TOKEN — writes a taskfile.env with GH_TOKEN=TOKEN.
make_gh_token_config() {
  local dir="$1"
  local token="$2"
  mkdir -p "$dir/.config/task"
  echo "GH_TOKEN=$token" > "$dir/.config/task/taskfile.env"
}

# make_git_repo_with_org DIR ORG REPO — creates a git repo with origin pointing to github.com:ORG/REPO.
make_git_repo_with_org() {
  local dir="$1"
  local org="$2"
  local repo="$3"
  mkdir -p "$dir"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  # (bats places TMPDIR under the repo tree, so git init would reuse the parent .git)
  GIT_CEILING_DIRECTORIES="$(dirname "$dir")" git -C "$dir" init --quiet
  git -C "$dir" remote add origin "git@github.com:${org}/${repo}.git"
}

# make_fake_binary DIR NAME — creates an executable stub in DIR/NAME.
make_fake_binary() {
  local dir="$1"
  local name="$2"
  mkdir -p "$dir"
  printf '#!/bin/bash\necho "fake output"\n' > "$dir/$name"
  chmod +x "$dir/$name"
}

# make_git_remote REMOTE_DIR LOCAL_DIR BRANCH — sets up a bare remote, clones it,
# makes an initial commit on main, then creates BRANCH with one commit.
make_git_remote() {
  local remote_dir="$1"
  local local_dir="$2"
  local branch="${3:-feature/test}"

  export GIT_CONFIG_GLOBAL=/dev/null
  GIT_CEILING_DIRECTORIES="$(dirname "$local_dir")"
  export GIT_CEILING_DIRECTORIES

  git init --bare "$remote_dir" --quiet --initial-branch=main
  git clone "$remote_dir" "$local_dir" --quiet 2>/dev/null

  [[ -d "$local_dir/.git" ]] || {
    echo "FATAL: git clone failed — $local_dir/.git does not exist"
    return 1
  }

  cd "$local_dir" || return 1
  _assert_not_real_repo || return 1

  git config user.email "test@example.com"
  git config user.name "Test"
  git config core.hooksPath /dev/null

  echo "init" > README.md
  git add .
  git commit -m "initial" --quiet
  git push --quiet

  git checkout -b "$branch" --quiet
  echo "feature" > feature.txt
  git add .
  git commit -m "feat: add feature" --quiet
}

# clone_from_shared_remote REMOTE_DIR LOCAL_DIR [BRANCH] — fast local clone from
# a bare remote created by make_git_remote in setup_file. Use this in per-test
# setup() to avoid repeating the expensive init/commit/push cycle.
clone_from_shared_remote() {
  local remote_dir="$1"
  local local_dir="$2"
  local branch="${3:-feature/test}"

  export GIT_CONFIG_GLOBAL=/dev/null
  GIT_CEILING_DIRECTORIES="$(dirname "$local_dir")"
  export GIT_CEILING_DIRECTORIES

  cd / || return 1
  git clone "$remote_dir" "$local_dir" --quiet 2>/dev/null

  [[ -d "$local_dir/.git" ]] || {
    echo "FATAL: git clone failed — $local_dir/.git does not exist"
    return 1
  }

  cd "$local_dir" || return 1
  _assert_not_real_repo || return 1

  git config user.email "test@example.com"
  git config user.name "Test"
  git config core.hooksPath /dev/null
  git checkout "$branch" --quiet 2>/dev/null
}
