#!/usr/bin/env bash
# adoption-sensitive: drains overrides/ai/claude/skills under the config root,
# which legacy-root adoption re-seeds — a NOOP here would retire the migration
# against a directory adoption puts back afterwards.
#
# Skills stopped being Claude-owned when they moved to ai/skills, so the layer
# that overrides them moves out of the harness-named directory with them.

migration_20260901_skills_override_root() {
  local old_root="$WORKBENCH_CONFIG_DIR/overrides/ai/claude/skills"
  local new_root="$WORKBENCH_CONFIG_DIR/overrides/ai/skills"

  [[ -d "$old_root" ]] || return "$MIGRATION_NOOP"

  if [[ -e "$new_root" ]]; then
    err "Both $old_root and $new_root exist — merge them by hand, then re-run sync"
    return 1
  fi

  mkdir -p "$(dirname "$new_root")"
  mv "$old_root" "$new_root"
  rmdir "$(dirname "$old_root")" 2> /dev/null || true
  return 0
}
