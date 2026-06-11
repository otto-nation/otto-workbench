#!/usr/bin/env bash
# should-promote.sh — checks whether a memory promotion review is due.
# Returns 0 (true) if ANY project is 7+ days AND 10+ sessions since last promote.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

PROMOTE_INTERVAL_HOURS=168  # 7 days
MIN_SESSIONS=10

now=$(date +%s)
threshold_secs=$((PROMOTE_INTERVAL_HOURS * 3600))

for project_dir in ~/.claude/projects/*/; do
  [ -d "$project_dir" ] || continue

  # Read this project's last-promote timestamp (default 0 = never promoted).
  stamp_file="${project_dir}memory/.last-promote"
  last_promote=0
  if [ -f "$stamp_file" ]; then
    last_promote=$(cat "$stamp_file" 2>/dev/null || echo 0)
  fi

  elapsed=$((now - last_promote))
  if [ "$elapsed" -lt "$threshold_secs" ]; then
    continue
  fi

  # Count sessions modified since last promote in THIS project only.
  session_count=0
  for session_file in "${project_dir}"*.jsonl; do
    [ -f "$session_file" ] || continue
    file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
    if [ "$file_ts" -gt "$last_promote" ]; then
      session_count=$((session_count + 1))
    fi
    if [ "$session_count" -ge "$MIN_SESSIONS" ]; then
      exit 0
    fi
  done
done

exit 1
