#!/usr/bin/env bash
# Session-counting helper for dream/promote cooldown checks.

# Sourced directly rather than via lib/ui.sh: the Stop hooks that call this
# helper skip ui.sh to stay inside their startup budget.
# shellcheck source=../portable.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/portable.sh"

# _has_enough_sessions PROJECT_DIR SINCE_TS MIN_COUNT
# Returns 0 if at least MIN_COUNT .jsonl files in PROJECT_DIR have mtime > SINCE_TS.
_has_enough_sessions() {
  local project_dir="$1" since="$2" min_count="$3"
  local count=0 session_file file_ts
  for session_file in "${project_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(file_mtime "$session_file") || file_ts=0
    if [[ "$file_ts" -gt "$since" ]]; then
      count=$((count + 1))
    fi
    if [[ "$count" -ge "$min_count" ]]; then
      return 0
    fi
  done
  return 1
}
