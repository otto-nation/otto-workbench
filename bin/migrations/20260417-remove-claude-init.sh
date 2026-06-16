#!/usr/bin/env bash
# Migration: remove claude-init symlink from ~/.local/bin/.
# Replaced by `otto-workbench ai init` for project scaffold.

migration_20260417_remove_claude_init() {
  local target="$LOCAL_BIN_DIR/claude-init"
  if [[ -L "$target" ]]; then
    rm "$target"
    info "Removed claude-init symlink (use 'otto-workbench ai init' instead)"
  fi
}
