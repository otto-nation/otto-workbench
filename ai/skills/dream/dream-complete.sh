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
# Usage: dream-complete.sh [--backup <project-slug>] [--root <invocation>]
#        --backup  Back up a project's memory directory before first dream run.
#        --root    Trail invocation of the dream-scan this closes, so the close
#                  is filed under that run rather than as a command of its own.
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
OPT_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup)
      [[ -n "${2:-}" ]] || { echo "Error: $1 requires an argument" >&2; exit 2; }
      OPT_BACKUP="$2"; shift 2 ;;
    --root)
      [[ -n "${2:-}" ]] || { echo "Error: $1 requires an argument" >&2; exit 2; }
      OPT_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# ── Safety backup ────────────────────────────────────────────────────────────

# Addressed by raw Claude slug, not by repo path through _memory_repos: this is
# a manual one-off a person runs before a first dream pass on a project they
# name directly, not a gate sweep over the registry — so there is no repo path
# on hand to resolve through, only the slug the operator already has in front
# of them (e.g. from `ls ~/.claude/projects`).
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
projects=0

while IFS=$'\t' read -r mem_dir _repo_dir; do
  [[ -n "$mem_dir" ]] || continue
  echo "$now" > "$mem_dir/.last-dream"
  projects=$((projects + 1))
done < <(_memory_repos)

# ── Record the close on the trail ────────────────────────────────────────────
# Best-effort by design: this runs from a Stop hook, and a trail write is a
# record of the dream rather than part of it. A workbench whose otto-log
# predates `record` — the state every machine is in until this ships — fails
# the subcommand, and that must not fail the dream it is reporting on.

# Whether the agent recorded anything between the scan and here. Nothing
# enforces that it did — the phases are instructions in SKILL.md, and a run that
# skipped them leaves a trail holding a scan and a close with nothing between,
# which reads as a dream that found nothing rather than one that never wrote it
# down. Reported, not enforced: the dream's real work is already on disk by now,
# and failing here would not bring the missing records back.
_warn_if_no_phases_recorded() {
  local otto_log="$1"
  [[ -n "$OPT_ROOT" ]] || return 0
  local phases
  phases=$("$otto_log" query --root "$OPT_ROOT" --script dream --json 2>/dev/null | wc -l | tr -d ' ')
  [[ "$phases" -gt 0 ]] && return 0
  echo "Note: no dream phase records under $OPT_ROOT — the trail will show" >&2
  echo "      this run's scan and close with nothing in between." >&2
}

_record_close() {
  local otto_log="$LOCAL_BIN_DIR/otto-log"
  [[ -x "$otto_log" ]] || return 0
  _warn_if_no_phases_recorded "$otto_log"
  WORKBENCH_TRAIL_ROOT="$OPT_ROOT" "$otto_log" record \
    --script dream --action close --detail "dream complete across $projects project(s)" \
    --data "projects=$projects" >/dev/null 2>&1 || true
}

_record_close
