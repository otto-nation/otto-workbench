#!/usr/bin/env bash
# promote-complete.sh — records promote timestamps and removes the pending flag.
#
# Writes .last-promote to every project with a memory directory and removes
# ~/.claude/.promote-pending. Called by the promote skill after Phase 4 completes.
#
# Usage: promote-complete.sh
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

# ── Record timestamps ────────────────────────────────────────────────────────

now=$(date +%s)

for mem_dir in "$HOME/.claude/projects"/*/memory/; do
  [[ -d "$mem_dir" ]] || continue
  echo "$now" > "${mem_dir}.last-promote"
done

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$HOME/.claude/.promote-pending"
