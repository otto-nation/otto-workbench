#!/usr/bin/env bash
# should-retro.sh — checks whether a retro analysis is due.
# Returns 0 (true) if 72+ hours AND 5+ sessions (in any project with memory/)
# since last retro. Projects without memory/ are skipped — they have no session
# activity to measure.
# Returns 1 (false) otherwise.
# Uses a global timestamp (~/.claude/.last-retro) since retro scans across all repos.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

RETRO_INTERVAL_HOURS=72
MIN_SESSIONS=5

now=$(date +%s)
threshold_secs=$((RETRO_INTERVAL_HOURS * 3600))

stamp_file="$CLAUDE_DIR/.last-retro"
last_retro=0
[[ -f "$stamp_file" ]] && last_retro=$(cat "$stamp_file" 2>/dev/null || echo 0)

elapsed=$((now - last_retro))
[[ "$elapsed" -lt "$threshold_secs" ]] && exit 1

for project_dir in "$CLAUDE_DIR/projects"/*/; do
  [[ -d "$project_dir" ]] || continue
  [[ -d "${project_dir}memory" ]] || continue

  if _has_enough_sessions "$project_dir" "$last_retro" "$MIN_SESSIONS"; then
    exit 0
  fi
done

exit 1
