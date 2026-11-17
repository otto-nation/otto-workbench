#!/usr/bin/env bash
set -e
# adoption-sensitive: drains <state>/batch/admission/ and
# <state>/batch/estimates.json, which adopt_legacy_workbench_root would
# re-seed from a legacy root. Without the marker an adoption after this
# migration is recorded would restore a registry nothing reads.
#
# Migration: drop the batch machine-admission registry. Every queued step is
# an otto-queue ticket now, so leftover admission JSON and RSS estimates are
# orphaned state. Idempotent — no-op if already gone.

migration_20261117_drop_batch_admission_registry() {
  # Defensive only: the framework always exports WORKBENCH_STATE_DIR, but
  # there is no `set -u` here, and an empty value would collapse the target
  # to "/batch" on a script that deletes files.
  [[ -n "$WORKBENCH_STATE_DIR" ]] || return "$MIGRATION_NOOP"

  local root="$WORKBENCH_STATE_DIR/batch"
  local admission="$root/admission"
  local changed=0

  if [[ -d "$admission" ]]; then
    rm -rf "$admission"
    changed=1
  fi
  local f
  for f in "$root/estimates.json" "$root/estimates.lock" "$root/estimates.json.lock"; do
    if [[ -e "$f" ]]; then
      rm -f "$f"
      changed=1
    fi
  done
  if [[ "$changed" -eq 0 ]]; then
    return "$MIGRATION_NOOP"
  fi
  success "Removed leftover batch admission registry under $root"
  return 0
}
