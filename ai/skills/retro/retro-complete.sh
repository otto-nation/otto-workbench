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
