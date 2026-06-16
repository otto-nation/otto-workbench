#!/usr/bin/env bash
# should-promote.sh — checks whether a memory promotion review is due.
# Returns 0 (true) if ANY project with a memory/ directory is 7+ days AND
# 10+ sessions since last promote. Projects without memory/ are skipped — they
# have nothing to promote and no timestamp file to record completion.
# Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit (~10ms overhead).

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

for project_dir in "$CLAUDE_DIR/projects"/*/; do
  [[ -d "$project_dir" ]] || continue
  [[ -d "${project_dir}memory" ]] || continue

  stamp_file="${project_dir}memory/.last-promote"
  last_promote=0
  [[ -f "$stamp_file" ]] && last_promote=$(cat "$stamp_file" 2>/dev/null || echo 0)

  elapsed=$((now - last_promote))
  [[ "$elapsed" -lt "$threshold_secs" ]] && continue

  if _has_enough_sessions "$project_dir" "$last_promote" "$MIN_SESSIONS"; then
    exit 0
  fi
done

exit 1
