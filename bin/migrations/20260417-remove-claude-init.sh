#!/usr/bin/env bash
# Migration: remove claude-init symlink from ~/.local/bin/.
# Replaced by `otto-workbench claude` which combines machine sync + project scaffold.

migration_20260417_remove_claude_init() {
  local target="$HOME/.local/bin/claude-init"
  if [[ -L "$target" ]]; then
    rm "$target"
    info "Removed claude-init symlink (use 'otto-workbench claude' instead)"
  fi
}
