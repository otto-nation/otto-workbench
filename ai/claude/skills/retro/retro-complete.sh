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

# ── Record timestamp ────────────────────────────────────────────────────────

date +%s > "$HOME/.claude/.last-retro"

# ── Clean up consumed reviews ───────────────────────────────────────────────

REVIEWS_DIR="$HOME/.config/workbench/reviews"
if [[ -d "$REVIEWS_DIR" ]]; then
  for review_dir in "$REVIEWS_DIR"/*/; do
    [[ -d "$review_dir" ]] || continue
    review_file="${review_dir}review.md"
    [[ -f "$review_file" ]] || continue
    rm -rf "$review_dir"
  done
fi

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$HOME/.claude/.retro-pending"
