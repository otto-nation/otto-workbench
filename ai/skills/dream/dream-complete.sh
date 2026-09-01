#!/usr/bin/env bash
# dream-complete.sh — records dream timestamps and removes the pending flag.
#
# Writes .last-dream to every project with a memory directory and removes
# ~/.claude/.dream-pending. Called by the dream skill after Phase 4 completes.
#
# Usage: dream-complete.sh [--backup <project-slug>]
#        --backup  Back up a project's memory directory before first dream run.
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/constants.sh"

OPT_BACKUP=""
for arg in "$@"; do
  case "$arg" in
    --backup) shift; OPT_BACKUP="${1:-}"; shift || true ;;
  esac
done

# ── Safety backup ────────────────────────────────────────────────────────────

_run_backup() {
  local slug="$1"
  local mem_dir="$CLAUDE_DIR/projects/$slug/memory"
  [[ -d "$mem_dir" ]] || return 0
  local backup_dir
  backup_dir="$CLAUDE_DIR/projects/$slug/memory-backup-$(date +%Y%m%d)"
  [[ -d "$backup_dir" ]] && return 0
  cp -r "$mem_dir" "$backup_dir"
}

[[ -n "$OPT_BACKUP" ]] && _run_backup "$OPT_BACKUP"

# ── Record timestamps ────────────────────────────────────────────────────────

now=$(date +%s)

for mem_dir in "$CLAUDE_DIR/projects"/*/memory/; do
  [[ -d "$mem_dir" ]] || continue
  echo "$now" > "${mem_dir}.last-dream"
done

# ── Remove pending flag ──────────────────────────────────────────────────────

rm -f "$CLAUDE_DIR/.dream-pending"
