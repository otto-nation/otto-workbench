#!/usr/bin/env bash
# promote-complete.sh — records promote timestamps.
#
# Writes .last-promote to every registered repo with a memory directory. Called
# by the promote skill after Phase 4 completes.
#
# The repos come from _memory_repos, which is the same sweep should-promote.sh
# reads to decide a promote is due. Globbing the memory directories here would
# be a second definition of that set: the gate skips a repo that has left the
# registry, so a glob would write stamps no gate ever reads back.
#
# Usage: promote-complete.sh
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

# ── Record timestamps ────────────────────────────────────────────────────────

now=$(date +%s)

while IFS=$'\t' read -r mem_dir _repo_dir; do
  [[ -n "$mem_dir" ]] || continue
  echo "$now" > "$mem_dir/.last-promote"
done < <(_memory_repos)
