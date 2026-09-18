#!/usr/bin/env bash
# should-wiki-capture.sh — checks whether a passive knowledge capture is due
# for the repo this session ran in.
#
# Returns 0 (true) when this repo has a knowledge base and it has been 24+ hours
# and 3+ sessions since the last capture. Returns 1 (false) otherwise.
# Used by the Stop hook: runs on every session exit.
#
# Scoped to the session's own repo rather than sweeping every project, unlike
# should-dream.sh. run-auto-task inherits this hook's cwd, so the headless
# session it spawns resolves its knowledge base from here — a gate that fired
# because some *other* project was overdue would capture this session's findings
# into the wrong wiki, which is the bug the ported plugin hook had.
#
# "This repo" means the repository, not the checkout: sessions for one repo are
# spread across a directory per worktree per harness, and the cooldown belongs
# to the knowledge base, which is one per repo. Resolving that costs a
# `git rev-parse`, and it has to come first because the stamp is filed under the
# answer. The three checks after it stay ordered cheapest-first — two file reads
# before the session sweep, and `wiki path` is a Python process so it runs last:
# a session in a repo with no wiki pays the fork and two reads, on every exit,
# forever.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

CAPTURE_INTERVAL_HOURS=24
MIN_SESSIONS=3

repo_dir="$(_gate_repo_dir "$PWD")"

stamp_file="$(_gate_stamp_file "$repo_dir" "last-wiki-capture")"
last_capture="$(_read_stamp "$stamp_file")"

now=$(date +%s)
threshold_secs=$((CAPTURE_INTERVAL_HOURS * 3600))
[[ $((now - last_capture)) -lt "$threshold_secs" ]] && exit 1

_repo_has_enough_sessions "$repo_dir" "$last_capture" "$MIN_SESSIONS" || exit 1

# Last, and only for a repo that has otherwise qualified: `wiki path` exits 2
# when there is no knowledge base, which is the ordinary answer rather than a
# failure — most repos have none and nothing here should be captured into one
# that does not exist.
wiki path > /dev/null 2>&1 || exit 1

exit 0
