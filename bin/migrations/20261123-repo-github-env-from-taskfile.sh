#!/usr/bin/env bash
# checkout-scoped: .taskfile/taskfile.env and .workbench/github.env live in
# each work tree. A machine-wide or repo-scoped line would skip later checkouts.
# Not adoption-sensitive: these files are in the checkout, not under the
# config or state root.
#
# Migration: copy <checkout>/.taskfile/taskfile.env to
# <checkout>/.workbench/github.env when the new file is absent.

migration_20261123_repo_github_env_from_taskfile() {
  local work_tree="$1"
  local dest="$work_tree/.workbench/github.env"
  local src="$work_tree/.taskfile/taskfile.env"
  if [[ -f "$dest" ]]; then
    return "$MIGRATION_NOOP"
  fi
  if [[ ! -f "$src" ]]; then
    return "$MIGRATION_DEFERRED"
  fi
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  chmod 600 "$dest"
  return 0
}
