#!/usr/bin/env bash
# should-retro.sh — checks whether a retro analysis is due.
# Returns 0 (true) if 72+ hours AND 5+ sessions (in any repo with memory/)
# since last retro. Repos without memory/ are skipped — they have no session
# activity to measure.
# Returns 1 (false) otherwise.
# Uses a global timestamp ($RETRO_STAMP_FILE) since retro scans across all repos.
#
# Sessions are counted per repo across every harness and every worktree — see
# the note in should-dream.sh. The stamp stays global because a retro is one
# sweep over every repo's reviews rather than a per-repo pass.

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

last_retro="$(_read_stamp "$RETRO_STAMP_FILE")"

elapsed=$((now - last_retro))
[[ "$elapsed" -lt "$threshold_secs" ]] && exit 1

while IFS=$'\t' read -r memory_dir repo_dir; do
  [[ -n "$memory_dir" ]] || continue

  if _repo_has_enough_sessions "$repo_dir" "$last_retro" "$MIN_SESSIONS"; then
    exit 0
  fi
done < <(_memory_repos)

exit 1
