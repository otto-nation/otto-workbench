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
_run_migration() {
  WORKBENCH_STATE_DIR="$STATE" bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    source "$1"
  ' _ "$MIGRATION"
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
  run wc -l < "$STATE/trail/legacy.jsonl"
  [ "${output// /}" = "2" ]
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
  mkdir -p "$STATE/logs/promote-scan"
  echo '{"ts":"noise"}' > "$STATE/logs/promote-scan/trail.jsonl"
  run _run_migration
  [ "$status" -eq 0 ]
  [ ! -d "$STATE/logs/promote-scan" ]
  run grep -c "noise" "$STATE/trail/legacy.jsonl"
  [ "$status" -ne 0 ]
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
