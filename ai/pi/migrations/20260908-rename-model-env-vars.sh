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
  [[ -f "$ENV_LOCAL_FILE" ]] || return "$MIGRATION_NOOP"

  local -A renames=(
    [ANTHROPIC_MODEL]=AI_MODEL
    [ANTHROPIC_DEFAULT_OPUS_MODEL]=AI_OPUS_MODEL
    [ANTHROPIC_DEFAULT_SONNET_MODEL]=AI_SONNET_MODEL
    [ANTHROPIC_DEFAULT_HAIKU_MODEL]=AI_HAIKU_MODEL
  )

  local changed=false old new
  for old in "${!renames[@]}"; do
    new="${renames[$old]}"
    # Skip if old is absent or new already exists
    grep -q "^export ${old}=" "$ENV_LOCAL_FILE" || continue
    if grep -q "^export ${new}=" "$ENV_LOCAL_FILE"; then
      continue
    fi
    # Rename active exports and commented-out template lines
    sed -i '' \
      -e "s/^export ${old}=/export ${new}=/" \
      -e "s/^# export ${old}=/# export ${new}=/" \
      "$ENV_LOCAL_FILE"
    changed=true
  done

  if [[ "$changed" == "true" ]]; then
    success "Renamed model env vars in $ENV_LOCAL_FILE (ANTHROPIC_* → AI_*)"
    return 0
  fi
  return "$MIGRATION_NOOP"
}
