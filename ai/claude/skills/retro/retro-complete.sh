#!/usr/bin/env bash
# retro-complete.sh — records retro timestamp, cleans up consumed reviews,
# and removes the pending flag.
#
# Writes .last-retro to ~/.claude/ (global, not per-project), deletes
# review directories from ~/.config/workbench/reviews/ that have been
# consumed by this retro run, and removes ~/.claude/.retro-pending.
# Called by the retro skill after Phase 4 completes.
#
# Usage: retro-complete.sh
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/constants.sh"

# ── Record timestamp ────────────────────────────────────────────────────────

date +%s > "$CLAUDE_DIR/.last-retro"

# ── Clean up consumed reviews ───────────────────────────────────────────────

REVIEWS_DIR="$WORKBENCH_STATE_DIR/reviews"
if [[ -d "$REVIEWS_DIR" ]]; then
  for review_dir in "$REVIEWS_DIR"/*/; do
    [[ -d "$review_dir" ]] || continue
    review_file="${review_dir}review.md"
    [[ -f "$review_file" ]] || continue
    rm -rf "$review_dir"
  done
fi

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$CLAUDE_DIR/.retro-pending"
