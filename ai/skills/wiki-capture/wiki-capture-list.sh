#!/usr/bin/env bash
# wiki-capture-list.sh — transcripts for this repo since last-wiki-capture.
#
# One absolute path per line, newest not required. Empty output is success:
# there is nothing to review, and the capture still stamps.
#
# Usage: wiki-capture-list.sh [DIR]
#        DIR defaults to the current directory.
#
# The current auto-task session is omitted when PI_SESSION_ID or
# CLAUDE_CODE_SESSION_ID is set, so a maintenance spawn does not review itself.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

repo_dir="$(_gate_repo_dir "${1:-$PWD}")"
stamp="$(_read_stamp "$(_gate_stamp_file "$repo_dir" "last-wiki-capture")")"
exclude_pi="${PI_SESSION_ID:-}"
exclude_claude="${CLAUDE_CODE_SESSION_ID:-}"

while IFS= read -r session_dir; do
  [[ -n "$session_dir" ]] || continue
  for session_file in "${session_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(file_mtime "$session_file") || file_ts=0
    [[ "$file_ts" -gt "$stamp" ]] || continue
    base="${session_file##*/}"
    base="${base%.jsonl}"
    # Pi files are {timestamp}_{id}.jsonl; Claude files are {id}.jsonl.
    [[ -n "$exclude_pi" && ( "$base" == "$exclude_pi" || "$base" == *_"$exclude_pi" ) ]] && continue
    [[ -n "$exclude_claude" && "$base" == "$exclude_claude" ]] && continue
    printf '%s\n' "$session_file"
  done
done < <(_session_dirs_for_repo "$repo_dir")

exit 0
