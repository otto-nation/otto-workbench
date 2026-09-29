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
# Usage: dream-complete.sh [--backup <repo-path>] [--root <invocation>]
#        --backup  Back up a repo's memory directory before first dream run.
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

# Takes a repo path, not a harness slug. Memory is keyed by repo identity now,
# so the operator names the repo they are about to dream over and the key is
# resolved the same way every other consumer resolves it. A path git cannot
# name a shared directory for has no memory to back up.
_run_backup() {
  local repo="$1" mem_dir backup_dir
  mem_dir="$(_memory_dir "$repo")" || return 0
  [[ -d "$mem_dir" ]] || return 0
  backup_dir="$mem_dir-backup-$(date +%Y%m%d)"
  [[ -d "$backup_dir" ]] && return 0
  cp -r "$mem_dir" "$backup_dir"
}

[[ -n "$OPT_BACKUP" ]] && _run_backup "$OPT_BACKUP"

# ── Record timestamps ────────────────────────────────────────────────────────

now=$(date +%s)
projects=0

# The stamps live under the gates root beside the other cooldowns, which on a
# machine that has never closed a gate does not exist yet.
mkdir -p "$GATE_STAMPS_DIR"

while IFS=$'\t' read -r _mem_dir repo_dir; do
  [[ -n "$repo_dir" ]] || continue
  echo "$now" > "$(_gate_stamp_file "$repo_dir" 'last-dream')"
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
  # Captured before counting: piping straight into `wc -l` reports the pipeline's
  # last command, so a query that died would count zero lines and be announced
  # as a run that recorded nothing.
  local found
  if ! found=$("$otto_log" query --root "$OPT_ROOT" --script dream --json 2>/dev/null); then
    return 0
  fi
  [[ -z "$found" ]] || return 0
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
