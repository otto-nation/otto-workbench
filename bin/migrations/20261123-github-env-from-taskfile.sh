#!/usr/bin/env bash
# adoption-sensitive: github.env is seeded from taskfile.env; legacy-root
# adoption re-seeds WORKBENCH_CONFIG_DIR and would otherwise leave this undone.
#
# Migration: copy ~/.config/task/taskfile.env to $WORKBENCH_CONFIG_DIR/github.env
# when the new file is absent. Copy, do not move — pr still reads the old path.

migration_20261123_github_env_from_taskfile() {
  local dest="${WORKBENCH_GITHUB_ENV:-}"
  local src="${TASKFILE_ENV:-}"
  if [[ -z "$dest" ]]; then
    warn "WORKBENCH_GITHUB_ENV is empty; not copying GitHub PAT files"
    return 1
  fi
  if [[ -f "$dest" ]]; then
    return "$MIGRATION_NOOP"
  fi
  if [[ -z "$src" || ! -f "$src" ]]; then
    return "$MIGRATION_DEFERRED"
  fi
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  chmod 600 "$dest"
  return 0
}
