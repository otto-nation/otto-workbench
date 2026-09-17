#!/usr/bin/env bash
# dream-complete.sh — records dream timestamps.
#
# Writes .last-dream to every registered repo with a memory directory. Called by
# the dream skill after Phase 4 completes.
#
# The repos come from _memory_repos, which is the same sweep should-dream.sh
# reads to decide a dream is due. Globbing the memory directories here would be
# a second definition of that set: the gate skips a repo that has left the
# registry, so a glob would write stamps no gate ever reads back.
#
# Usage: dream-complete.sh [--backup <project-slug>]
#        --backup  Back up a project's memory directory before first dream run.
#
# Exit codes:
#   0 — completed successfully
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_WB="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
. "$_WB/lib/constants.sh"
. "$_WB/lib/ai/session-count.sh"
unset _WB

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

while IFS=$'\t' read -r mem_dir _repo_dir; do
  [[ -n "$mem_dir" ]] || continue
  echo "$now" > "$mem_dir/.last-dream"
done < <(_memory_repos)
