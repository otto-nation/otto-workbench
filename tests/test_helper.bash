#!/usr/bin/env bash
# Shared setup helpers for workbench bats tests.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
LIB="$REPO_ROOT/lib/ai-commit.sh"

# source_lib — loads lib/ai-commit.sh into the current test context.
source_lib() {
  # shellcheck disable=SC1090
  source "$LIB"
}

# make_ai_config DIR COMMAND — writes a taskfile.env with AI_COMMAND=COMMAND.
make_ai_config() {
  local dir="$1"
  local command="$2"
  mkdir -p "$dir/.config/task"
  echo "AI_COMMAND=$command" > "$dir/.config/task/taskfile.env"
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

  git init --bare "$remote_dir" --quiet
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
