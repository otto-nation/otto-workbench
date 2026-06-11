#!/usr/bin/env bash
# should-dream.sh — checks whether a dream consolidation is due.
# Returns 0 (true) if ANY project is 24+ hours AND 5+ sessions since last dream.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

DREAM_INTERVAL_HOURS=24
MIN_SESSIONS=5

now=$(date +%s)
threshold_secs=$((DREAM_INTERVAL_HOURS * 3600))

for project_dir in ~/.claude/projects/*/; do
  [ -d "$project_dir" ] || continue

  # Read this project's last-dream timestamp (default 0 = never dreamed).
  stamp_file="${project_dir}memory/.last-dream"
  last_dream=0
  if [ -f "$stamp_file" ]; then
    last_dream=$(cat "$stamp_file" 2>/dev/null || echo 0)
  fi

  elapsed=$((now - last_dream))
  if [ "$elapsed" -lt "$threshold_secs" ]; then
    continue
  fi

  # Count sessions modified since last dream in THIS project only.
  session_count=0
  for session_file in "${project_dir}"*.jsonl; do
    [ -f "$session_file" ] || continue
    file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
    if [ "$file_ts" -gt "$last_dream" ]; then
      session_count=$((session_count + 1))
    fi
    if [ "$session_count" -ge "$MIN_SESSIONS" ]; then
      exit 0
    fi
  done
done

exit 1
