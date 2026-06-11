#!/usr/bin/env bash
# Session-counting helper for dream/promote cooldown checks.

# _has_enough_sessions PROJECT_DIR SINCE_TS MIN_COUNT
# Returns 0 if at least MIN_COUNT .jsonl files in PROJECT_DIR have mtime > SINCE_TS.
_has_enough_sessions() {
  local project_dir="$1" since="$2" min_count="$3"
  local count=0
  for session_file in "${project_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(stat -c %Y "$session_file" 2>/dev/null || stat -f %m "$session_file" 2>/dev/null || echo 0)
    if [[ "$file_ts" -gt "$since" ]]; then
      count=$((count + 1))
    fi
    if [[ "$count" -ge "$min_count" ]]; then
      return 0
    fi
  done
  return 1
}
