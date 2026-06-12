#!/usr/bin/env bash
# retro-complete.sh — records retro timestamp and removes the pending flag.
#
# Writes .last-retro to ~/.claude/ (global, not per-project) and removes
# ~/.claude/.retro-pending. Called by the retro skill after Phase 4 completes.
#
# Usage: retro-complete.sh
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

# ── Record timestamp ────────────────────────────────────────────────────────

date +%s > "$HOME/.claude/.last-retro"

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$HOME/.claude/.retro-pending"
