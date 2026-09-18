#!/usr/bin/env bash
# should-dream.sh — checks whether a dream consolidation is due.
# Returns 0 (true) if ANY repo with a memory/ directory is 24+ hours AND
# 5+ sessions since last dream. Repos without memory/ are skipped — they
# have nothing to consolidate and no timestamp file to record completion.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).
#
# Sessions are counted per repo across every harness and every worktree, by
# _repo_has_enough_sessions. Counting one Claude directory asked whether a
# single worktree had been busy under a single harness, so a week spent in Pi
# across six worktrees tripped nothing — which is why the last recorded dream
# on this machine predated the move to Pi by two and a half months.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

DREAM_INTERVAL_HOURS=24
MIN_SESSIONS=5

now=$(date +%s)
threshold_secs=$((DREAM_INTERVAL_HOURS * 3600))

while IFS=$'\t' read -r memory_dir repo_dir; do
  [[ -n "$memory_dir" ]] || continue

  last_dream="$(_read_stamp "$memory_dir/.last-dream")"

  elapsed=$((now - last_dream))
  [[ "$elapsed" -lt "$threshold_secs" ]] && continue

  if _repo_has_enough_sessions "$repo_dir" "$last_dream" "$MIN_SESSIONS"; then
    exit 0
  fi
done < <(_memory_repos)

exit 1
