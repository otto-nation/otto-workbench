#!/usr/bin/env bash
# should-dream.sh — checks whether a dream consolidation is due.
# Returns 0 (true) if ANY project is 24+ hours AND 5+ sessions since last dream.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(dirname "$_SELF")/../../../../lib/ai/session-count.sh"

DREAM_INTERVAL_HOURS=24
MIN_SESSIONS=5

now=$(date +%s)
threshold_secs=$((DREAM_INTERVAL_HOURS * 3600))

for project_dir in ~/.claude/projects/*/; do
  [[ -d "$project_dir" ]] || continue

  stamp_file="${project_dir}memory/.last-dream"
  last_dream=0
  [[ -f "$stamp_file" ]] && last_dream=$(cat "$stamp_file" 2>/dev/null || echo 0)

  elapsed=$((now - last_dream))
  [[ "$elapsed" -lt "$threshold_secs" ]] && continue

  if _has_enough_sessions "$project_dir" "$last_dream" "$MIN_SESSIONS"; then
    exit 0
  fi
done

exit 1
