#!/usr/bin/env bash
# Shared setup helpers for workbench bats tests.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

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
  git -C "$dir" init --quiet
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

  # Isolate from user's global gitconfig (e.g. empty gpg.format causes failures).
  export GIT_CONFIG_GLOBAL=/dev/null

  git init --bare "$remote_dir" --quiet --initial-branch=main
  git clone "$remote_dir" "$local_dir" --quiet 2>/dev/null
  cd "$local_dir" || return 1

  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > README.md
  git add .
  git commit -m "initial" --quiet
  git push --quiet

  git checkout -b "$branch" --quiet
  echo "feature" > feature.txt
  git add .
  git commit -m "feat: add feature" --quiet
}
