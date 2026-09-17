#!/usr/bin/env bash
# should-promote.sh — checks whether a memory promotion review is due.
# Returns 0 (true) if ANY repo with a memory/ directory is 7+ days AND
# 10+ sessions since last promote. Repos without memory/ are skipped — they
# have nothing to promote and no timestamp file to record completion.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).
#
# Sessions are counted per repo across every harness and every worktree — see
# the note in should-dream.sh, which this gate mirrors at a longer interval.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

PROMOTE_INTERVAL_HOURS=168  # 7 days
MIN_SESSIONS=10

now=$(date +%s)
threshold_secs=$((PROMOTE_INTERVAL_HOURS * 3600))

while IFS=$'\t' read -r memory_dir repo_dir; do
  [[ -n "$memory_dir" ]] || continue

  last_promote="$(_read_stamp "$memory_dir/.last-promote")"

  elapsed=$((now - last_promote))
  [[ "$elapsed" -lt "$threshold_secs" ]] && continue

  if _repo_has_enough_sessions "$repo_dir" "$last_promote" "$MIN_SESSIONS"; then
    exit 0
  fi
done < <(_memory_repos)

exit 1
