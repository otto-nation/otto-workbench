#!/usr/bin/env bash
# Migration: remove the go skill symlink from ~/.claude/skills/.
# The go skill was removed — it referenced unregistered MCPs and added no value.
# Idempotent — no-op if the symlink does not exist.

migration_20260407_remove_go_skill() {
  local target="$CLAUDE_SKILLS_DIR/go"

  if [[ -e "$target" || -L "$target" ]]; then
    rm -rf "$target"
    success "Removed go skill from $target"
  else
    success "go skill already absent"
  fi
}
