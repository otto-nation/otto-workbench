#!/usr/bin/env bash
# Migration: update include path after git/.gitconfig → git/gitconfig.shared rename.
# Replaces the stale include path in ~/.gitconfig. Idempotent — no-op if already correct.

migration_20260402_shared_config_rename() {
  [[ -f "$GITCONFIG_FILE" ]] || return 0
  grep -qF "git/.gitconfig" "$GITCONFIG_FILE" || return 0

  sed_i 's|git/\.gitconfig|git/gitconfig.shared|' "$GITCONFIG_FILE"
  success "Updated include path: git/.gitconfig → git/gitconfig.shared"
}
