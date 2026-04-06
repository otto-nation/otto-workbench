#!/usr/bin/env bash
# should-dream.sh — checks whether a dream consolidation is due.
# Returns 0 (true) if 24+ hours AND 5+ sessions since last dream.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

set -e

DREAM_INTERVAL_HOURS=24
MIN_SESSIONS=5

# Find the most recent .last-dream timestamp across all projects.
last_dream=0
for stamp_file in ~/.claude/projects/*/memory/.last-dream; do
  [[ -f "$stamp_file" ]] || continue
  ts=$(cat "$stamp_file" 2>/dev/null || echo 0)
  if [[ "$ts" -gt "$last_dream" ]]; then
    last_dream=$ts
  fi
done

now=$(date +%s)
elapsed_hours=$(( (now - last_dream) / 3600 ))

if [[ "$elapsed_hours" -lt "$DREAM_INTERVAL_HOURS" ]]; then
  exit 1
fi

# Count sessions modified since last dream.
session_count=0
for session_file in ~/.claude/projects/*/sessions/*.jsonl; do
  [[ -f "$session_file" ]] || continue
  file_ts=$(stat -f %m "$session_file" 2>/dev/null || stat -c %Y "$session_file" 2>/dev/null || echo 0)
  if [[ "$file_ts" -gt "$last_dream" ]]; then
    session_count=$((session_count + 1))
  fi
  if [[ "$session_count" -ge "$MIN_SESSIONS" ]]; then
    exit 0
  fi
done

exit 1
