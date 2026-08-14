#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260814-unify-trail-root.sh — folds review
# trails into trail/legacy.jsonl and drops the bats residue under logs/.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
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
# migration calls but does not define itself — is in scope, matching how the
# framework actually invokes it (see the framework-dispatch test below for
# the source-and-call double-invocation this harness does not exercise).
_run_migration() {
  WORKBENCH_STATE_DIR="$STATE" bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$WORKBENCH_STATE_DIR/.unused-legacy"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    source "$1"
  ' _ "$MIGRATION" "$REPO_ROOT"
}

_seed_review_trail() {
  local name="$1" body="$2"
  mkdir -p "$STATE/reviews/$name"
  printf '%s' "$body" > "$STATE/reviews/$name/trail.jsonl"
  echo "artifact" > "$STATE/reviews/$name/review.md"
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
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [ -f "$STATE/reviews/repo-1/trail.jsonl" ]
  run grep -c '"ts":"b"' "$STATE/trail/legacy.jsonl"
  [ "$output" = "1" ]

  chmod 644 "$STATE/reviews/repo-1/trail.jsonl"
}

@test "carries a trail through the framework's real source-and-call dispatch" {
  # _run_migration above sources the file once in a fresh bash -c, always
  # under errexit. The real framework (lib/migrations.sh) sources AND calls,
  # running the function twice in one process — the second time with errexit
  # suppressed, since that call is the condition of an `if`. Build a fake
  # workbench root, matching the tests/migrations.bats FAKE_ROOT pattern, and
  # go through run_all_migrations for real.
  local fake_root="$STATE/fake-workbench"
  local fake_state="$STATE/fake-state"
  mkdir -p "$fake_root/lib" "$fake_root/ai/claude/migrations" \
    "$fake_state/reviews/repo-1"
  printf '{"ts":"a"}\n' > "$fake_state/reviews/repo-1/trail.jsonl"
  cp "$REPO_ROOT/lib/components.sh" "$fake_root/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$fake_root/lib/migrations.sh"
  cp "$MIGRATION" "$fake_root/ai/claude/migrations/20260814-unify-trail-root.sh"

  cat > "$fake_root/lib/ui.sh" <<'STUB'
success() { echo "OK $*"; }
warn()    { echo "WARN $*"; }
STUB

  cat > "$fake_root/lib/constants.sh" <<CONST
WORKBENCH_DIR="$fake_root"
LIB_SRC_DIR="$fake_root/lib"
WORKBENCH_STATE_DIR="$fake_state"
WORKBENCH_CONFIG_DIR="$STATE/fake-config"
LEGACY_WORKBENCH_ROOT="$STATE/fake-legacy"
MIGRATIONS_STATE_FILE="$fake_state/migrations.applied"
CONST

  run bash -c '
    . "$1/lib/ui.sh"
    . "$1/lib/constants.sh"
    . "$1/lib/migrations.sh"
    run_all_migrations
  ' _ "$fake_root"

  [ "$status" -eq 0 ]
  [ ! -f "$fake_state/reviews/repo-1/trail.jsonl" ]
  run grep -c '"ts"' "$fake_state/trail/legacy.jsonl"
  [ "$output" = "1" ]
}
