#!/usr/bin/env bash
# Migration: remove ~/.kiro/ directory.
# Kiro support has been removed from the workbench — clean up steering
# symlinks and agent configs that were installed by previous syncs.
# Idempotent — no-op if the directory does not exist.

migration_20260417_remove_kiro() {
  local target="$HOME/.kiro"

  if [[ -d "$target" ]]; then
    rm -rf "$target"
    success "Removed $target"
  else
    success "$target already absent"
  fi
}
