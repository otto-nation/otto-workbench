#!/usr/bin/env bash
# Repository layouts shared by the projects_* suites.

# make_repo DIR — a git work tree at DIR.
make_repo() {
  mkdir -p "$1"
  GIT_CEILING_DIRECTORIES="$(dirname "$1")" git -C "$1" init --quiet
}

# make_bare_container DIR — the layout wt-init produces: a bare repo at
# DIR/.git with per-branch worktrees beside it.
make_bare_container() {
  mkdir -p "$1"
  git init --bare --quiet "$1/.git"
}

# make_bare_worktree_layout DIR — a bare container with the branch its HEAD
# names checked out at DIR/main, and a feature worktree beside it.
make_bare_worktree_layout() {
  local container="$1" seed="$1.seed"
  make_repo "$seed"
  git -C "$seed" -c user.email=t@example.com -c user.name=t commit --allow-empty -qm init
  git -C "$seed" branch -qM main
  mkdir -p "$container"
  git clone --bare --quiet "$seed" "$container/.git"
  git --git-dir="$container/.git" worktree add "$container/main" main >/dev/null 2>&1
  git --git-dir="$container/.git" worktree add -b feature "$container/feature" >/dev/null 2>&1
  rm -rf "$seed"
}
