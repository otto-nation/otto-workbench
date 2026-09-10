#!/usr/bin/env bash
# Migration: rename model env vars in ~/.env.local from ANTHROPIC_* to AI_*.
#
# The workbench now uses generic AI_* names as the single source of truth for
# model configuration across all harnesses (Claude Code, Pi). The old
# ANTHROPIC_* names were Claude Code-specific; the new names are
# harness-neutral. Each harness sync maps them to its own format:
#   Claude Code: writes ANTHROPIC_* into ~/.claude/settings.json env block
#   Pi:          writes defaultModel + enabledModels into Pi's settings.json
#
# Only renames when the old name exists and the new name does not, so the
# migration is idempotent and does not clobber values the operator already set
# under the new names.

migration_20260908_rename_model_env_vars() {
  # A fresh machine has no ~/.env.local yet — step_env_local creates it later
  # in this same sync. Returning MIGRATION_NOOP here would retire the rename
  # against a file it never saw, so an operator who restores a personal
  # ~/.env.local afterward, still carrying the old ANTHROPIC_* names, would
  # never get it renamed and the new AI_* readers would silently see nothing.
  [[ -f "$ENV_LOCAL_FILE" ]] || return "$MIGRATION_DEFERRED"

  local -A renames=(
    [ANTHROPIC_MODEL]=AI_MODEL
    [ANTHROPIC_DEFAULT_OPUS_MODEL]=AI_OPUS_MODEL
    [ANTHROPIC_DEFAULT_SONNET_MODEL]=AI_SONNET_MODEL
    [ANTHROPIC_DEFAULT_HAIKU_MODEL]=AI_HAIKU_MODEL
  )

  local changed=false old new
  for old in "${!renames[@]}"; do
    new="${renames[$old]}"
    # Skip if old is absent (neither active nor commented) or new already exists
    grep -qE "^(export |# export )${old}=" "$ENV_LOCAL_FILE" || continue
    if grep -q "^export ${new}=" "$ENV_LOCAL_FILE"; then
      continue
    fi
    # Renames the active export and the commented-out template line in one
    # pass; the optional group carries whichever prefix the line had.
    #
    # Through sed_i rather than `sed -i ''`, which is BSD-only: GNU reads the
    # empty string as the script and edits nothing, so the rename silently
    # does not happen on Linux.
    sed_i -E "s/^(# )?export ${old}=/\1export ${new}=/" "$ENV_LOCAL_FILE"
    changed=true
  done

  if [[ "$changed" == "true" ]]; then
    success "Renamed model env vars in $ENV_LOCAL_FILE (ANTHROPIC_* → AI_*)"
    return 0
  fi
  return "$MIGRATION_NOOP"
}
