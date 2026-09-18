#!/usr/bin/env bash
# retro-complete.sh — records the retro timestamp and cleans up consumed reviews.
#
# Writes the global retro stamp (one per machine, not per project) and deletes
# the review directories the scan recorded under SCAN_ID.
# Called by the retro skill after Phase 4 completes.
#
# The scan ID is required because the deletion is not this script's decision to
# make: retro-scan --consume records what it read and stamps the record with
# the ID it printed, and retro-consume honours that record only for the scan
# that wrote it. Passing the ID through is how a completion says which retro it
# is completing — without it, an abandoned run's record or a debug scan's would
# be honoured just the same.
#
# The same ID is the scan's trail root, so the close this script records lands
# under the retro it completes rather than as a command of its own.
#
# Usage: retro-complete.sh SCAN_ID
#
# Exit codes:
#   0 — completed successfully
#   1 — the consume record belongs to a different scan
#   2 — no scan ID given

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_REPO_ROOT="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"
# shellcheck source=/dev/null
. "$_REPO_ROOT/lib/constants.sh"

SCAN_ID="${1:-}"
if [[ -z "$SCAN_ID" ]]; then
  echo "Error: retro-complete.sh requires the scan ID retro-scan --consume reported" >&2
  echo "Usage: retro-complete.sh SCAN_ID" >&2
  exit 2
fi

# ── Clean up consumed reviews ───────────────────────────────────────────────
# Before the timestamp, so a refused record fails the completion rather than
# banking the window on the strength of a cleanup that did not happen.

"$_REPO_ROOT/ai/bin/retro-consume" --scan-id "$SCAN_ID"

# ── Record timestamp ────────────────────────────────────────────────────────

mkdir -p "$GATE_STAMPS_DIR"
date +%s > "$RETRO_STAMP_FILE"

# ── Record the close on the trail ────────────────────────────────────────────
# Best-effort by design: this runs from a Stop hook, and a trail write is a
# record of the retro rather than part of it. A workbench whose otto-log
# predates `record` — the state every machine is in until this ships — fails
# the subcommand, and that must not fail the retro it is reporting on.

# Whether the agent recorded anything between the scan and here. Nothing
# enforces that it did — the phases are instructions in SKILL.md, and a run that
# skipped them leaves a trail holding a scan and a close with nothing between,
# which reads as a retro that proposed nothing rather than one that never wrote
# it down. Reported, not enforced: the retro's real work is already on disk by
# now, and failing here would not bring the missing records back.
_warn_if_no_phases_recorded() {
  local otto_log="$1"
  # Captured before counting: piping straight into `wc -l` reports the pipeline's
  # last command, so a query that died would count zero lines and be announced
  # as a run that recorded nothing.
  local found
  if ! found=$("$otto_log" query --root "$SCAN_ID" --script retro --json 2>/dev/null); then
    return 0
  fi
  [[ -z "$found" ]] || return 0
  echo "Note: no retro phase records under $SCAN_ID — the trail will show" >&2
  echo "      this run's scan and close with nothing in between." >&2
}

_record_close() {
  local otto_log="$LOCAL_BIN_DIR/otto-log"
  [[ -x "$otto_log" ]] || return 0
  _warn_if_no_phases_recorded "$otto_log"
  WORKBENCH_TRAIL_ROOT="$SCAN_ID" "$otto_log" record \
    --script retro --action close --detail "retro complete" >/dev/null 2>&1 || true
}

_record_close
