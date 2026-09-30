#!/usr/bin/env bash
# Migration: remove the claude-review symlink from ~/.local/bin/.
# The command is now `review`: the engine picks its backend from config
# (claude or pi), so a Claude-branded name was the last thing claiming
# otherwise.
#
# `symlink_dir --prune` only removes links whose source is gone from the
# source directory it is syncing. ai/bin/claude-review was renamed rather than
# deleted, so from the pruner's side nothing was removed and the stale link
# survives every sync until something names it.

migration_20260930_remove_claude_review() {
  local target="$LOCAL_BIN_DIR/claude-review"
  if [[ -L "$target" ]]; then
    rm "$target"
    info "Removed claude-review symlink (the command is now 'review')"
  fi
}
