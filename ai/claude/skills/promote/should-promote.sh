#!/usr/bin/env bash
# should-promote.sh — checks whether a memory promotion review is due.
# Returns 0 (true) if ANY project is 7+ days AND 10+ sessions since last promote.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

PROMOTE_INTERVAL_HOURS=168  # 7 days
MIN_SESSIONS=10

_has_enough_sessions() {
  local project_dir="$1" since="$2"
  local count=0
  for session_file in "${project_dir}"*.jsonl; do
    [ -f "$session_file" ] || continue
    file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
    if [ "$file_ts" -gt "$since" ]; then
      count=$((count + 1))
    fi
    if [ "$count" -ge "$MIN_SESSIONS" ]; then
      return 0
    fi
  done
  return 1
}

now=$(date +%s)
threshold_secs=$((PROMOTE_INTERVAL_HOURS * 3600))

for project_dir in ~/.claude/projects/*/; do
  [ -d "$project_dir" ] || continue

  stamp_file="${project_dir}memory/.last-promote"
  last_promote=0
  [ -f "$stamp_file" ] && last_promote=$(cat "$stamp_file" 2>/dev/null || echo 0)

  elapsed=$((now - last_promote))
  [ "$elapsed" -lt "$threshold_secs" ] && continue

  if _has_enough_sessions "$project_dir" "$last_promote"; then
    exit 0
  fi
done

exit 1
