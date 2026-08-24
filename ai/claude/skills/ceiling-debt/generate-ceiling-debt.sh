#!/usr/bin/env bash
# generate-ceiling-debt.sh — refreshes .claude/ceiling-debt.md for the session's repo.
#
# Runs on session stop. Resolves the repo root first: a session rooted at a
# bare-repo container has no working tree, so the scan runs against the worktree
# the container stands in for rather than finding nothing and saying nothing.
#
# Usage: generate-ceiling-debt.sh [DIR]
#        DIR defaults to the current directory.
#
# Exit codes:
#   0 — ledger refreshed, or there is nothing here to scan
#   1 — a repo was found but the ledger could not be refreshed; the reason is on
#       stderr, which Claude Code surfaces only for a non-zero exit
#
# Never exits 2: for a Stop hook that means "block the stop", which a ledger
# refresh has no business doing.

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
# ui.sh rather than constants.sh alone: it is what puts warn and the workbench
# source paths in scope at once.
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"

RESOLVE_WORKTREE="$BIN_SRC_DIR/resolve-worktree"
CEILING_SCAN="$CLAUDE_SRC_DIR/bin/ceiling-scan"

target="${1:-$PWD}"

# The working tree the session is in, when it is in one at all.
root="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null)" || root=""

if [[ -z "$root" ]]; then
  rc=0
  root="$("$RESOLVE_WORKTREE" "$target")" || rc=$?
  # 2 — not a bare repository either, so there is no repo here and no ledger to
  # refresh. Every non-repo directory a session runs in lands here.
  if [[ "$rc" -eq 2 ]]; then
    exit 0
  fi
  # err rather than warn: only stderr reaches the user from a hook, and a skip
  # nobody sees is the failure this script exists to stop repeating.
  if [[ "$rc" -ne 0 ]]; then
    err "ceiling-debt: no worktree resolved for $target — ledger not refreshed"
    exit 1
  fi
fi

# The ledger lives in .claude/, so a repo that has none is not opted in.
[[ -d "$root/.claude" ]] || exit 0

python3 "$CEILING_SCAN" --output "$root/.claude/ceiling-debt.md" "$root" || {
  err "ceiling-debt: ceiling-scan failed for $root — ledger not refreshed"
  exit 1
}
