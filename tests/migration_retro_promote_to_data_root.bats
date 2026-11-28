#!/usr/bin/env bats
# Move retro/promote reports off the checkout onto the data root.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIG="$REPO_ROOT/bin/migrations/20261128-retro-promote-to-data-root.sh"
  STABLE="$TMPDIR/stable"
  DATA="$TMPDIR/data"
  mkdir -p "$STABLE/ai/memory" "$DATA"
}

teardown() {
  common_teardown
}

_run_mig() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    WORKBENCH_STABLE_DIR="$3"
    WORKBENCH_DATA_DIR="$4"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    RETRO_DIR="$4/retro"
    RETRO_ARCHIVE_DIR="$4/retro/archive"
    PROMOTE_DIR="$4/promote"
    . "$1"
    migration_20261128_retro_promote_to_data_root
  ' _ "$MIG" "$REPO_ROOT" "$STABLE" "$DATA"
}

@test "moves RETRO.md, its archive, and PROMOTE.md onto the data root" {
  printf 'live retro\n' > "$STABLE/ai/memory/RETRO.md"
  mkdir -p "$STABLE/ai/memory/retro"
  printf 'old scan\n' > "$STABLE/ai/memory/retro/abc123.md"
  printf 'live promote\n' > "$STABLE/ai/memory/PROMOTE.md"

  run _run_mig
  [ "$status" -eq 0 ]
  grep -qx 'live retro' "$DATA/retro/RETRO.md"
  grep -qx 'old scan' "$DATA/retro/archive/abc123.md"
  grep -qx 'live promote' "$DATA/promote/PROMOTE.md"
  [ ! -e "$STABLE/ai/memory/RETRO.md" ]
  [ ! -e "$STABLE/ai/memory/PROMOTE.md" ]
  [ ! -d "$STABLE/ai/memory/retro" ]
}

@test "leaves a source file when the destination already exists" {
  mkdir -p "$DATA/retro"
  printf 'already there\n' > "$DATA/retro/RETRO.md"
  printf 'checkout copy\n' > "$STABLE/ai/memory/RETRO.md"

  run _run_mig
  grep -qx 'already there' "$DATA/retro/RETRO.md"
  grep -qx 'checkout copy' "$STABLE/ai/memory/RETRO.md"
}

@test "is a no-op when the checkout has no reports" {
  run _run_mig
  [ "$status" -eq 3 ]
  [ ! -e "$DATA/retro/RETRO.md" ]
  [ ! -e "$DATA/promote/PROMOTE.md" ]
}
