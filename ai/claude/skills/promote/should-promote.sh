#!/usr/bin/env bash
# should-promote.sh — checks whether a memory promotion review is due.
# Returns 0 (true) if 7+ days AND 10+ sessions since last promote.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

PROMOTE_INTERVAL_HOURS=168  # 7 days
MIN_SESSIONS=10

# Find the most recent .last-promote timestamp across all projects.
last_promote=0
for stamp_file in ~/.claude/projects/*/memory/.last-promote; do
  [[ -f "$stamp_file" ]] || continue
  ts=$(cat "$stamp_file" 2>/dev/null || echo 0)
  if [[ "$ts" -gt "$last_promote" ]]; then
    last_promote=$ts
  fi
done

now=$(date +%s)
elapsed_hours=$(( (now - last_promote) / 3600 ))

if [[ "$elapsed_hours" -lt "$PROMOTE_INTERVAL_HOURS" ]]; then
  exit 1
fi

# Count sessions modified since last promote.
session_count=0
for session_file in ~/.claude/projects/*/sessions/*.jsonl; do
  [[ -f "$session_file" ]] || continue
  file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
  if [[ "$file_ts" -gt "$last_promote" ]]; then
    session_count=$((session_count + 1))
  fi
  if [[ "$session_count" -ge "$MIN_SESSIONS" ]]; then
    exit 0
  fi
done

exit 1
