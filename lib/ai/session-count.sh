#!/usr/bin/env bash
# Session-counting helper for dream/promote cooldown checks.

# _has_enough_sessions PROJECT_DIR SINCE_TS MIN_COUNT
# Returns 0 if at least MIN_COUNT .jsonl files in PROJECT_DIR have mtime > SINCE_TS.
_has_enough_sessions() {
  local project_dir="$1" since="$2" min_count="$3"
  local count=0 session_file file_ts
  for session_file in "${project_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    # GNU form first, then BSD, each assigned separately. Chaining both inside
    # one substitution would capture the output of a form that writes to stdout
    # before failing — GNU stat does exactly that for -f, which it reads as
    # --file-system.
    file_ts=$(stat -c %Y "$session_file" 2>/dev/null) \
      || file_ts=$(stat -f %m "$session_file" 2>/dev/null) \
      || file_ts=0
    if [[ "$file_ts" -gt "$since" ]]; then
      count=$((count + 1))
    fi
    if [[ "$count" -ge "$min_count" ]]; then
      return 0
    fi
  done
  return 1
}
