#!/usr/bin/env bash
# wiki-capture-complete.sh — records that a passive capture reviewed this repo.
#
# Writes .last-wiki-capture into the repo's ~/.claude/projects directory, which
# is what should-wiki-capture.sh reads to decide whether another is due.
# Called by the wiki-capture skill once it has finished reviewing, whether or
# not it logged anything: the cooldown resets on the review having happened, not
# on it having found something. A capture that logged nothing and did not stamp
# would re-run on every session exit for the rest of the day.
#
# Usage: wiki-capture-complete.sh [DIR]
#        DIR defaults to the current directory.
#
# Exit codes:
#   0 — recorded, or there is no project directory to record against
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

project_dir="$(_claude_project_dir "${1:-$PWD}")"
[[ -d "$project_dir" ]] || exit 0

date +%s > "$project_dir/.last-wiki-capture"
