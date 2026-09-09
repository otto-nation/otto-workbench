#!/usr/bin/env bash
# Session-counting and project-directory helpers for the Stop-hook cooldown
# gates, and for the completion scripts that reset them.

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

# _claude_project_dir DIR — prints the ~/.claude/projects directory holding the
# session transcripts for the repo at DIR, whether or not it exists.
#
# Claude Code names that directory for the absolute path of the session's cwd,
# with every character outside [A-Za-z0-9] replaced by a hyphen. Spelled here
# rather than at each caller so the gate and the completion script that resets
# it cannot disagree about which stamp file they mean.
#
# ceiling: the transform is what Claude Code does today and is not a documented
# contract. Upgrade to reading the directory off the hook payload if a session
# ever resolves to a directory that is not there.
_claude_project_dir() {
  local slug
  slug="$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '-')"
  printf '%s/projects/%s' "$CLAUDE_DIR" "$slug"
}
