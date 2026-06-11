#!/usr/bin/env bash
# should-dream.sh — checks whether a dream consolidation is due.
# Returns 0 (true) if ANY project is 24+ hours AND 5+ sessions since last dream.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

DREAM_INTERVAL_HOURS=24
MIN_SESSIONS=5

_has_enough_sessions() {
  local project_dir="$1" since="$2"
  local count=0
  for session_file in "${project_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
    if [[ "$file_ts" -gt "$since" ]]; then
      count=$((count + 1))
    fi
    if [[ "$count" -ge "$MIN_SESSIONS" ]]; then
      return 0
    fi
  done
  return 1
}

now=$(date +%s)
threshold_secs=$((DREAM_INTERVAL_HOURS * 3600))

for project_dir in ~/.claude/projects/*/; do
  [[ -d "$project_dir" ]] || continue

  stamp_file="${project_dir}memory/.last-dream"
  last_dream=0
  [[ -f "$stamp_file" ]] && last_dream=$(cat "$stamp_file" 2>/dev/null || echo 0)

  elapsed=$((now - last_dream))
  [[ "$elapsed" -lt "$threshold_secs" ]] && continue

  if _has_enough_sessions "$project_dir" "$last_dream"; then
    exit 0
  fi
done

exit 1
