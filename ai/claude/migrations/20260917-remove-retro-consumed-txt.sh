#!/usr/bin/env bash
# Migration: remove the legacy plain-text consumed-reviews record.
# retro-scan wrote retro-consumed-reviews.txt before the scan-ID-stamped JSON
# manifest in ai/lib/retro/consumed.py replaced it (see
# tests/workbench_roots.bats for the current name). Nothing reads or writes
# the .txt file anymore, so a machine that ran the old retro-scan is left
# with it as orphaned state. Idempotent — no-op if already removed.

migration_20260917_remove_retro_consumed_txt() {
    # Defensive only: the framework always exports WORKBENCH_STATE_DIR, but
    # there is no `set -u` here, and an empty value would collapse the target
    # to "/retro-consumed-reviews.txt" on a script that deletes files.
    [[ -n "$WORKBENCH_STATE_DIR" ]] || return "$MIGRATION_NOOP"

    local legacy="$WORKBENCH_STATE_DIR/retro-consumed-reviews.txt"
    [[ -f "$legacy" ]] || return "$MIGRATION_NOOP"

    rm -f "$legacy"
    success "Removed legacy $legacy"
}
