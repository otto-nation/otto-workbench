#!/usr/bin/env bash
# Migration: relocate user overrides from $WORKBENCH_DIR/user/ai/ to
# $WORKBENCH_STATE_DIR/overrides/ai/ (~/.config/workbench/overrides/ai/).
# Idempotent — no-op if already migrated or nothing to move.

migration_20260513_relocate_user_overrides() {
  local old="$WORKBENCH_DIR/user/ai"
  local new="$WORKBENCH_STATE_DIR/overrides/ai"

  if [[ ! -d "$old" ]] || [[ -z "$(ls -A "$old" 2>/dev/null)" ]]; then
    success "No user overrides to migrate"
    return 0
  fi

  if [[ -d "$new" ]]; then
    success "Overrides already at $new"
    return 0
  fi

  mkdir -p "$WORKBENCH_STATE_DIR/overrides"
  mv "$old" "$new"
  success "Moved user overrides to $new"

  # Clean up empty parent
  rmdir "$WORKBENCH_DIR/user" 2>/dev/null || true
}
