#!/usr/bin/env bash
# wiki-capture-complete.sh — records that a passive capture reviewed this repo.
#
# Writes the repo's last-wiki-capture stamp under $GATE_STAMPS_DIR, which is
# what should-wiki-capture.sh reads to decide whether another is due.
# Called by the wiki-capture skill once it has finished reviewing, whether or
# not it logged anything: the cooldown resets on the review having happened, not
# on it having found something. A capture that logged nothing and did not stamp
# would re-run on every session exit for the rest of the day.
#
# The stamp is keyed by repo, so a capture run from one worktree settles the
# cooldown for all of them — matching the knowledge base, which is one per repo
# rather than one per checkout.
#
# Usage: wiki-capture-complete.sh [DIR]
#        DIR defaults to the current directory.
#
# Exit codes:
#   0 — recorded
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

repo_dir="$(_gate_repo_dir "${1:-$PWD}")"

mkdir -p "$GATE_STAMPS_DIR"
date +%s > "$(_gate_stamp_file "$repo_dir" "last-wiki-capture")"
