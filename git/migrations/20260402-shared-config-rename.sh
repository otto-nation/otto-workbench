#!/usr/bin/env bash
# Migration: update include path after git/.gitconfig → git/gitconfig.shared rename.
# Replaces the stale include path in ~/.gitconfig. Idempotent — no-op if already correct.

migration_20260402_shared_config_rename() {
  # No ~/.gitconfig yet: the git component writes one later in this same sync,
  # and a machine that restores an old dotfile can produce the stale include at
  # any time after that. Recording the absence would retire the rewrite for good.
  [[ -f "$GITCONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"
  # The file is here and carries no stale path — nothing writes the old one any
  # more, so this answer holds.
  grep -qF "git/.gitconfig" "$GITCONFIG_FILE" || return "$MIGRATION_NOOP"

  sed_i 's|git/\.gitconfig|git/gitconfig.shared|' "$GITCONFIG_FILE"
  success "Updated include path: git/.gitconfig → git/gitconfig.shared"
}
