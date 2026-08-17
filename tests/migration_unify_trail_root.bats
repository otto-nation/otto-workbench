#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260814-unify-trail-root.sh — folds review
# trails into trail/legacy.jsonl and drops the bats residue under logs/.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260814-unify-trail-root.sh"
  STATE="$(mktemp -d)"
  mkdir -p "$STATE/reviews" "$STATE/logs"
}

teardown() {
  rm -rf "$STATE"
  common_teardown
}

# Runs the migration against STATE with the ui.sh helpers stubbed out.
# Sources the real lib/migrations.sh first so _append_ledger — which the
# migration calls but does not define itself — is in scope, then sources the
# migration and calls its function, which is what lib/migrations.sh:69,76
# does. lib/portable.sh comes along because the real ui.sh sources it and
# _append_ledger calls file_mode from it; stubbing ui.sh down to success/warn
# would leave that call undefined and silently skip the chmod it guards.
# Exit status is the function's own: the framework reads it to decide whether
# to record the migration (see the framework-dispatch tests below).
_run_migration() {
  WORKBENCH_STATE_DIR="$STATE" bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$WORKBENCH_STATE_DIR/.unused-legacy"
    . "$WORKBENCH_DIR/lib/portable.sh"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20260814_unify_trail_root
  ' _ "$MIGRATION" "$REPO_ROOT"
}

_seed_review_trail() {
  local name="$1" body="$2"
  mkdir -p "$STATE/reviews/$name"
  printf '%s' "$body" > "$STATE/reviews/$name/trail.jsonl"
  echo "artifact" > "$STATE/reviews/$name/review.md"
}

# Builds a fake workbench root under $STATE/fake-$1{,-state,-config,-legacy}
# with the real lib/components.sh and lib/migrations.sh plus this migration
# copied in, and stubbed lib/ui.sh + lib/constants.sh. Sets FAKE_ROOT and
# FAKE_STATE for the caller. Used by tests about what the framework does with
# the function's exit status — discovery, recording, and retry — which
# _run_migration above, calling the function directly, cannot observe.
_build_fake_workbench() {
  local name="$1"
  FAKE_ROOT="$STATE/fake-$name"
  FAKE_STATE="$STATE/fake-$name-state"
  mkdir -p "$FAKE_ROOT/lib" "$FAKE_ROOT/ai/claude/migrations" "$FAKE_STATE/reviews"
  cp "$REPO_ROOT/lib/components.sh" "$FAKE_ROOT/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$FAKE_ROOT/lib/migrations.sh"
  cp "$REPO_ROOT/lib/portable.sh" "$FAKE_ROOT/lib/portable.sh"
  cp "$MIGRATION" "$FAKE_ROOT/ai/claude/migrations/20260814-unify-trail-root.sh"

  # The stub carries portable.sh because the real ui.sh does, and _append_ledger
  # calls file_mode from it.
  cat > "$FAKE_ROOT/lib/ui.sh" <<'STUB'
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/portable.sh"
success() { echo "OK $*"; }
warn()    { echo "WARN $*"; }
STUB

  cat > "$FAKE_ROOT/lib/constants.sh" <<CONST
WORKBENCH_DIR="$FAKE_ROOT"
LIB_SRC_DIR="$FAKE_ROOT/lib"
WORKBENCH_STATE_DIR="$FAKE_STATE"
WORKBENCH_CONFIG_DIR="$STATE/fake-$name-config"
LEGACY_WORKBENCH_ROOT="$STATE/fake-$name-legacy"
MIGRATIONS_STATE_FILE="$FAKE_STATE/migrations.applied"
CONST
}

_run_all_migrations_in_fake() {
  bash -c '
    . "$1/lib/ui.sh"
    . "$1/lib/constants.sh"
    . "$1/lib/migrations.sh"
    run_all_migrations
  ' _ "$FAKE_ROOT"
}

@test "folds every review trail into legacy.jsonl" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  _seed_review_trail "repo-2" '{"ts":"b"}
'
  run _run_migration
  [ "$status" -eq 0 ]
  run grep -c '"ts"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "2" ]
}

@test "a source with no trailing newline does not fuse two records" {
  _seed_review_trail "repo-1" '{"ts":"a"}'
  _seed_review_trail "repo-2" '{"ts":"b"}'
  run _run_migration
  [ "$status" -eq 0 ]
  # grep -c, not wc -l: _append_ledger normalizes the join between the
  # existing destination and the newly appended source, not the very end of
  # the file — when the last source carried also lacks a trailing newline,
  # legacy.jsonl legitimately ends without one too, and wc -l undercounts a
  # final line that has no terminating newline.
  run grep -c '"ts"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "2" ]
}

@test "review directories and their other artifacts stay" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  run _run_migration
  [ "$status" -eq 0 ]
  [ -f "$STATE/reviews/repo-1/review.md" ]
  [ ! -f "$STATE/reviews/repo-1/trail.jsonl" ]
}

@test "deletes log trails and their emptied tool directories" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  mkdir -p "$STATE/logs/promote-scan"
  echo '{"ts":"noise"}' > "$STATE/logs/promote-scan/trail.jsonl"
  run _run_migration
  [ "$status" -eq 0 ]
  [ ! -d "$STATE/logs/promote-scan" ]
  run grep -c "noise" "$STATE/trail/legacy.jsonl"
  [ "$status" -eq 1 ]
}

@test "a logs directory with other files keeps the directory" {
  mkdir -p "$STATE/logs/promote-scan"
  echo '{"ts":"noise"}' > "$STATE/logs/promote-scan/trail.jsonl"
  echo "keep" > "$STATE/logs/promote-scan/notes.txt"
  run _run_migration
  [ "$status" -eq 0 ]
  [ -f "$STATE/logs/promote-scan/notes.txt" ]
  [ ! -f "$STATE/logs/promote-scan/trail.jsonl" ]
}

@test "a second run is a no-op" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  run _run_migration
  [ "$status" -eq 0 ]
  run _run_migration
  [ "$status" -eq 0 ]
  run grep -c '"ts"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
}

@test "nothing to carry is not a failure" {
  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"No trails to carry"* ]]
}

@test "nothing to carry leaves no empty legacy.jsonl behind" {
  # legacy.jsonl has no month in its name, so otto-log reads it on every query
  # whatever the --since window — a machine with no pre-cutover history must
  # not be left paying that forever for an empty file.
  run _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$STATE/trail/legacy.jsonl" ]
}

@test "carrying preserves the destination's mode rather than mktemp's 0600" {
  # _append_ledger builds the merged file with mktemp (0600) and restores the
  # destination's mode from file_mode. Nothing else covers that line on this
  # destination, and a break in it would quietly narrow legacy.jsonl.
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  mkdir -p "$STATE/trail"
  printf '{"ts":"z"}\n' > "$STATE/trail/legacy.jsonl"
  chmod 640 "$STATE/trail/legacy.jsonl"

  run _run_migration
  [ "$status" -eq 0 ]
  run file_mode "$STATE/trail/legacy.jsonl"
  [ "$output" = "640" ]
}

@test "a resumed run does not duplicate an already-carried record" {
  # Mirrors "adoption resumes a run that was interrupted partway through a
  # directory" in tests/migrations.bats: repo-1 already finished its carry in
  # a prior partial run (record in legacy.jsonl, source gone), repo-2 is
  # still pending (source present). Resuming must add repo-2 exactly once
  # without touching repo-1's record.
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  _seed_review_trail "repo-2" '{"ts":"b"}
'
  mkdir -p "$STATE/trail"
  printf '{"ts":"a"}\n' > "$STATE/trail/legacy.jsonl"
  rm -f "$STATE/reviews/repo-1/trail.jsonl"

  run _run_migration
  [ "$status" -eq 0 ]
  run grep -c '"ts":"a"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
  run grep -c '"ts":"b"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
}

@test "a newline-less destination does not fuse with the carried record" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  mkdir -p "$STATE/trail"
  printf '{"ts":"z"}' > "$STATE/trail/legacy.jsonl"

  run _run_migration
  [ "$status" -eq 0 ]
  run wc -l < "$STATE/trail/legacy.jsonl"
  [ "${output// /}" = "2" ]
  run grep -c '"ts"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "2" ]
}

@test "an unreadable source warns, is skipped, and the rest of the carry finishes" {
  _seed_review_trail "repo-1" '{"ts":"a"}
'
  _seed_review_trail "repo-2" '{"ts":"b"}
'
  chmod 000 "$STATE/reviews/repo-1/trail.jsonl"

  run _run_migration
  # Non-zero on purpose: the carry finished for every source it could read,
  # and the status is how the framework learns not to record the migration
  # (asserted end-to-end in the retry test below).
  [ "$status" -eq 1 ]
  [[ "$output" == *"WARN"* ]]
  [ -f "$STATE/reviews/repo-1/trail.jsonl" ]
  run grep -c '"ts":"b"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]

  chmod 644 "$STATE/reviews/repo-1/trail.jsonl"
}

@test "carries a trail through the framework's real discover-and-dispatch" {
  # _run_migration above calls the function itself, in a fresh bash -c under
  # errexit. The real framework (lib/migrations.sh) has to find the file by
  # its name, derive the function name from it, source it, and call it as the
  # condition of an `if` — where errexit is suppressed. Build a fake workbench
  # root, matching the tests/migrations.bats FAKE_ROOT pattern, and go through
  # run_all_migrations for real.
  _build_fake_workbench "dispatch"
  mkdir -p "$FAKE_STATE/reviews/repo-1"
  printf '{"ts":"a"}\n' > "$FAKE_STATE/reviews/repo-1/trail.jsonl"

  run _run_all_migrations_in_fake

  [ "$status" -eq 0 ]
  [ ! -f "$FAKE_STATE/reviews/repo-1/trail.jsonl" ]
  run grep -c '"ts"' "$FAKE_STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
}

@test "an unreadable source leaves the migration unrecorded and retryable" {
  # Only the framework-dispatch harness can observe this: lib/migrations.sh
  # records a migration as applied (MIGRATIONS_STATE_FILE) only when the
  # explicit "$fn_name" call returns 0, and skips recording — with "will
  # retry on next run" — otherwise. repo-1 stays unreadable across the whole
  # first sync; repo-2 must still be carried in that same run.
  _build_fake_workbench "retry"
  mkdir -p "$FAKE_STATE/reviews/repo-1" "$FAKE_STATE/reviews/repo-2"
  printf '{"ts":"a"}\n' > "$FAKE_STATE/reviews/repo-1/trail.jsonl"
  printf '{"ts":"b"}\n' > "$FAKE_STATE/reviews/repo-2/trail.jsonl"
  chmod 000 "$FAKE_STATE/reviews/repo-1/trail.jsonl"

  run _run_all_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"will retry on next run"* ]]
  [ -f "$FAKE_STATE/reviews/repo-1/trail.jsonl" ]
  [ ! -f "$FAKE_STATE/reviews/repo-2/trail.jsonl" ]
  run grep -c '"ts":"b"' "$FAKE_STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
  run grep -qxF "ai/claude/20260814-unify-trail-root.sh" "$FAKE_STATE/migrations.applied"
  [ "$status" -ne 0 ]

  # Fixing the permission and re-syncing must pick up exactly what failed.
  chmod 644 "$FAKE_STATE/reviews/repo-1/trail.jsonl"
  run _run_all_migrations_in_fake
  [ "$status" -eq 0 ]
  [ ! -f "$FAKE_STATE/reviews/repo-1/trail.jsonl" ]
  run grep -c '"ts":"a"' "$FAKE_STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]
  run grep -qxF "ai/claude/20260814-unify-trail-root.sh" "$FAKE_STATE/migrations.applied"
  [ "$status" -eq 0 ]
}
