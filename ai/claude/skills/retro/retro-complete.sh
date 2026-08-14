#!/usr/bin/env bash
# retro-complete.sh — records retro timestamp, cleans up consumed reviews,
# and removes the pending flag.
#
# Writes .last-retro to ~/.claude/ (global, not per-project), deletes the
# review directories under $REVIEWS_DIR that have been consumed by this retro
# run, and removes ~/.claude/.retro-pending.
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
# Only delete review dirs listed in the consumed file written by retro-scan.

if [[ -f "$RETRO_CONSUMED_REVIEWS_FILE" ]] && [[ -d "$REVIEWS_DIR" ]]; then
  while IFS= read -r dir_name; do
    [[ -z "$dir_name" ]] && continue
    target="$REVIEWS_DIR/$dir_name"
    [[ -d "$target" ]] && rm -rf "$target"
  done < "$RETRO_CONSUMED_REVIEWS_FILE"
  rm -f "$RETRO_CONSUMED_REVIEWS_FILE"
fi

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$CLAUDE_DIR/.retro-pending"
