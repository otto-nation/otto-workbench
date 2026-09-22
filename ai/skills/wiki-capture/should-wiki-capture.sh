#!/usr/bin/env bash
# should-wiki-capture.sh — checks whether a passive knowledge capture is due
# for one repo.
#
# Usage: should-wiki-capture.sh [DIR]   (DIR defaults to $PWD)
#
# Returns 0 (true) when that repo has a knowledge base and it has been 24+ hours
# and 3+ sessions since the last capture. Returns 1 (false) otherwise.
#
# Two callers. The Stop hook asks about the session's own repo and passes
# nothing, taking $PWD: run-auto-task inherits the hook's cwd, so the headless
# session it spawns resolves its knowledge base from there — a gate that fired
# because some *other* project was overdue would capture this session's findings
# into the wrong wiki, which is the bug the ported plugin hook had. The
# maintenance timer has no session and no meaningful cwd — launchd gives it `/` —
# so it sweeps the registry and names the repo outright. Hence DIR: scoped to one
# repo either way, unlike should-dream.sh, but the scope is no longer only
# discoverable from where the process happens to be standing.
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

_inside_auto_task && exit 1

CAPTURE_INTERVAL_HOURS=24
MIN_SESSIONS=3

# Two different scopes off one input, and they are not interchangeable. The
# stamp and the session count belong to the repo, which every worktree shares;
# `wiki path` walks *up* from where it is told and stops at the first `.git`, so
# it has to be given a checkout. _gate_repo_dir resolves a bare-repo layout to
# the container above the worktrees, and a wiki committed inside one is not
# visible from there.
work_tree="${1:-$PWD}"
repo_dir="$(_gate_repo_dir "$work_tree")"

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
# Asked about the checkout rather than the cwd, so a caller that passed DIR gets
# an answer about the repo it named instead of wherever it happens to be
# standing — under launchd that is `/`, which has no wiki and never would.
wiki path "$work_tree" > /dev/null 2>&1 || exit 1

exit 0
