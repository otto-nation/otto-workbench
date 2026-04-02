#!/bin/bash
# Migration: remove stale [include] for ~/.gitconfig.local.
# The old architecture stored identity in ~/.gitconfig.local and included it from
# ~/.gitconfig. The new architecture puts identity directly in ~/.gitconfig.
# Idempotent — no-op if the include is not present.

migration_20260402_remove_local_config_include() {
  [[ -f "$GITCONFIG_FILE" ]] || return 0

  local local_config="$HOME/.gitconfig.local"

  # Remove the [include] + path stanza for .gitconfig.local
  if grep -qF ".gitconfig.local" "$GITCONFIG_FILE"; then
    # Remove the path line and any preceding [include] that belongs to it.
    # Pattern: blank line + [include] + tab-path = ...gitconfig.local
    sed -i '' '/\.gitconfig\.local/d' "$GITCONFIG_FILE"
    # Clean up orphaned [include] blocks left with no path= line after them.
    # An [include] followed by a blank line or another section header is orphaned.
    sed -i '' -E '/^\[include\]$/{N;/^\[include\]\n([[:space:]]*$|\[)/s/^\[include\]\n//;}' "$GITCONFIG_FILE"

    success "Removed stale include for .gitconfig.local"
  fi

  # Warn if the old file has content the user should merge
  if [[ -f "$local_config" ]] && [[ -s "$local_config" ]]; then
    warn "$local_config still exists — merge its contents into $GITCONFIG_FILE and delete it"
  fi
}
