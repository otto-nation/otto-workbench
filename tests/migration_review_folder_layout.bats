#!/usr/bin/env bats
# Tests for the legacy review layout migration.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  REVIEWS="$TMPDIR/state/reviews"
  # CLAUDE_DIR is derived from $HOME by constants.sh rather than read from the
  # environment, so the legacy root is placed by faking HOME for the migration
  # itself — same as tests/migration_claude_bin_paths.bats. Exporting CLAUDE_DIR
  # here would be overwritten the moment ui.sh sources constants.sh, and every
  # home-drain assertion would then read an empty real ~/.claude and pass.
  FAKE_HOME="$TMPDIR/home"
  CLAUDE_REVIEWS="$FAKE_HOME/.claude/reviews"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260915-review-folder-layout.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _run_migration — sources the migration the way lib/migrations.sh does and
# calls its function, returning its exit status. REVIEWS_DIR is derived from
# WORKBENCH_STATE_DIR by constants.sh, which ui.sh sources, so the export in
# setup() is what places the reviews root; CLAUDE_DIR comes off HOME the same
# way.
_run_migration() {
  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20260915_review_folder_layout
  "
}

@test "is a no-op when neither legacy layout is present" {
  _run_migration
  [ "$status" -eq 3 ]
}

@test "is a no-op when the reviews root holds only the folder layout" {
  mkdir -p "$REVIEWS/repo-42"
  echo "body" > "$REVIEWS/repo-42/review.md"

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
}

@test "carries reviews out of Claude's home into the state root" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS"
  echo "body" > "$CLAUDE_REVIEWS/repo-42.md"
  echo "session" > "$CLAUDE_REVIEWS/repo-42.session.jsonl"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$CLAUDE_REVIEWS" ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo-42/session.jsonl")" = "session" ]
}

@test "a flat destination already in place wins over Claude's home" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS"
  echo "stale" > "$CLAUDE_REVIEWS/repo-42.md"
  echo "live" > "$REVIEWS/repo-42.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "live" ]
}

# The Python this replaces compared against the flat name only, so the home
# copy was carried in beside a folder that already held the live review — flat
# litter the reshape then skipped for good, because the directory was there.
@test "a folder already in place wins over Claude's home" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS/repo-42"
  echo "stale" > "$CLAUDE_REVIEWS/repo-42.md"
  echo "stalesession" > "$CLAUDE_REVIEWS/repo-42.session.jsonl"
  echo "live" > "$REVIEWS/repo-42/review.md"

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "live" ]
  [ ! -e "$REVIEWS/repo-42.md" ]
  [ ! -e "$REVIEWS/repo-42.session.jsonl" ]
  [ "$(cat "$CLAUDE_REVIEWS/repo-42.md")" = "stale" ]
}

# A stamped artifact does not end in the plain suffix, so it needs its own case
# to strip back past the stamp. Without one it names a review that never
# existed, never matches the folder holding the live review, and is carried in
# as litter the reshape skips.
@test "a stamped archive in Claude's home resolves to its owning review" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS/repo-42"
  echo "live" > "$REVIEWS/repo-42/review.md"
  echo "oldsession" > "$CLAUDE_REVIEWS/repo-42.session.20260101-120000.jsonl"
  echo "oldpost" > "$CLAUDE_REVIEWS/repo-42.post.20260101-120000.jsonl"

  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$REVIEWS/repo-42.session.20260101-120000.jsonl" ]
  [ ! -e "$REVIEWS/repo-42.post.20260101-120000.jsonl" ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "live" ]
}

# The owning review is found by matching a closed set of historical suffixes,
# so a review whose own name holds a dot must not be read as an artifact of a
# shorter one.
@test "a dotted review name is not read as another review's artifact" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS/repo"
  echo "other" > "$REVIEWS/repo/review.md"
  echo "body" > "$CLAUDE_REVIEWS/repo.2fa-42.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo.2fa-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo/review.md")" = "other" ]
}

@test "gives a flat review the folder layout with all its artifacts" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo-42.md"
  echo "session" > "$REVIEWS/repo-42.session.jsonl"
  echo "meta" > "$REVIEWS/repo-42.meta.json"
  echo "post" > "$REVIEWS/repo-42.post.jsonl"
  echo "pipeline" > "$REVIEWS/repo-42.pipeline.json"
  echo "prior" > "$REVIEWS/repo-42.prior.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo-42/session.jsonl")" = "session" ]
  [ "$(cat "$REVIEWS/repo-42/meta.json")" = "meta" ]
  [ "$(cat "$REVIEWS/repo-42/post.jsonl")" = "post" ]
  [ "$(cat "$REVIEWS/repo-42/pipeline.json")" = "pipeline" ]
  [ "$(cat "$REVIEWS/repo-42/prior.md")" = "prior" ]
  [ ! -e "$REVIEWS/repo-42.md" ]
}

@test "carries the per-phase intermediates without their review prefix" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo-42.md"
  echo "g1" > "$REVIEWS/repo-42.group-1.md"
  echo "g1log" > "$REVIEWS/repo-42.group-1.jsonl"
  echo "hol" > "$REVIEWS/repo-42.holistic.md"
  echo "hollog" > "$REVIEWS/repo-42.holistic.jsonl"
  echo "syn" > "$REVIEWS/repo-42.synthesis.jsonl"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-42/group-1.md")" = "g1" ]
  [ "$(cat "$REVIEWS/repo-42/group-1.jsonl")" = "g1log" ]
  [ "$(cat "$REVIEWS/repo-42/holistic.md")" = "hol" ]
  [ "$(cat "$REVIEWS/repo-42/holistic.jsonl")" = "hollog" ]
  [ "$(cat "$REVIEWS/repo-42/synthesis.jsonl")" = "syn" ]
}

@test "puts timestamped prior runs under archives/" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo-42.md"
  echo "old" > "$REVIEWS/repo-42.20260101-120000.md"
  echo "oldsession" > "$REVIEWS/repo-42.session.20260101-120000.jsonl"
  echo "oldpost" > "$REVIEWS/repo-42.post.20260101-120000.jsonl"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-42/archives/20260101-120000.md")" = "old" ]
  [ "$(cat "$REVIEWS/repo-42/archives/session.20260101-120000.jsonl")" = "oldsession" ]
  [ "$(cat "$REVIEWS/repo-42/archives/post.20260101-120000.jsonl")" = "oldpost" ]
}

@test "creates no archives/ for a review that has none" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo-42.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$REVIEWS/repo-42/archives" ]
}

# The .md artifacts sort ahead of the review they belong to, so a loop that
# reshaped every *.md would give each of them a directory of its own — named
# after the artifact, holding the artifact as its deliverable — and the real
# review would then find that directory present and be skipped entirely.
@test "does not mistake a review's own .md artifacts for reviews" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo-42.md"
  echo "prior" > "$REVIEWS/repo-42.prior.md"
  echo "g1" > "$REVIEWS/repo-42.group-1.md"
  echo "hol" > "$REVIEWS/repo-42.holistic.md"
  echo "old" > "$REVIEWS/repo-42.20260101-120000.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$REVIEWS/repo-42.prior" ]
  [ ! -e "$REVIEWS/repo-42.group-1" ]
  [ ! -e "$REVIEWS/repo-42.holistic" ]
  [ ! -e "$REVIEWS/repo-42.20260101-120000" ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo-42/prior.md")" = "prior" ]
  [ "$(cat "$REVIEWS/repo-42/archives/20260101-120000.md")" = "old" ]
}

# The archive stamp is matched digit by digit rather than as .2*.md, so a repo
# whose name puts a dot before a 2 stays a review of its own.
@test "reshapes a review whose name holds a dot before a digit" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo.2fa-42.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo.2fa-42/review.md")" = "body" ]
}

# The archive sweep globs everything under the review's name and filters with
# _owning_review, so a sibling review that merely extends a shorter name is not
# swept up as one of its archives. Unfiltered, repo.md claims repo.notes.md and
# buries it at repo/archives/notes.md — and because the shorter name sorts
# first, the sibling is gone before the loop ever reaches it.
@test "does not archive a sibling review that extends a shorter name" {
  mkdir -p "$REVIEWS"
  echo "short" > "$REVIEWS/repo.md"
  echo "sibling" > "$REVIEWS/repo.notes.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo/review.md")" = "short" ]
  [ "$(cat "$REVIEWS/repo.notes/review.md")" = "sibling" ]
  [ ! -e "$REVIEWS/repo/archives" ]
}

# Both sides have to agree on what a stamp looks like. A .2*.md glob in the
# reshape takes the sibling review as an archive of the shorter one and buries
# it under archives/, where the review system never looks — even though
# _owning_review classified it correctly a moment earlier.
@test "does not bury a sibling review as a shorter review's archive" {
  mkdir -p "$REVIEWS"
  echo "short" > "$REVIEWS/repo.md"
  echo "sibling" > "$REVIEWS/repo.2fa-42.md"
  echo "old" > "$REVIEWS/repo.20260101-120000.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo/review.md")" = "short" ]
  [ "$(cat "$REVIEWS/repo.2fa-42/review.md")" = "sibling" ]
  [ "$(cat "$REVIEWS/repo/archives/20260101-120000.md")" = "old" ]
  [ ! -e "$REVIEWS/repo/archives/2fa-42.md" ]
}

@test "leaves a flat review alone when its folder already exists" {
  mkdir -p "$REVIEWS/repo-42"
  echo "live" > "$REVIEWS/repo-42/review.md"
  echo "stale" > "$REVIEWS/repo-42.md"

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "live" ]
  [ "$(cat "$REVIEWS/repo-42.md")" = "stale" ]
}

# The ordering the single-file form exists to guarantee: a review carried out
# of Claude's home lands flat, and the reshape below has to see it in the same
# run. Split across two migration files this would depend on filename order.
@test "reshapes what the home drain deposits, in one run" {
  mkdir -p "$CLAUDE_REVIEWS"
  echo "body" > "$CLAUDE_REVIEWS/repo-42.md"
  echo "session" > "$CLAUDE_REVIEWS/repo-42.session.jsonl"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo-42/session.jsonl")" = "session" ]
  [ ! -e "$REVIEWS/repo-42.md" ]
}

@test "reshapes several reviews in one run" {
  mkdir -p "$REVIEWS"
  echo "a" > "$REVIEWS/repo-1.md"
  echo "b" > "$REVIEWS/repo-2.md"
  echo "c" > "$REVIEWS/other-3.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo-1/review.md")" = "a" ]
  [ "$(cat "$REVIEWS/repo-2/review.md")" = "b" ]
  [ "$(cat "$REVIEWS/other-3/review.md")" = "c" ]
}

@test "is idempotent — a second run reports no work" {
  mkdir -p "$CLAUDE_REVIEWS" "$REVIEWS"
  echo "body" > "$CLAUDE_REVIEWS/repo-42.md"
  echo "flat" > "$REVIEWS/repo-7.md"

  _run_migration
  [ "$status" -eq 0 ]
  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$REVIEWS/repo-42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo-7/review.md")" = "flat" ]
}

@test "carries a review whose name holds a space" {
  mkdir -p "$REVIEWS"
  echo "body" > "$REVIEWS/repo 42.md"
  echo "session" > "$REVIEWS/repo 42.session.jsonl"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REVIEWS/repo 42/review.md")" = "body" ]
  [ "$(cat "$REVIEWS/repo 42/session.jsonl")" = "session" ]
}

@test "declares itself adoption-sensitive" {
  # Runtime contract, not a validator check: lib/migrations.sh drops the state
  # entry of a marked migration after adoption, so an adoption that re-seeds
  # flat reviews under the state root gets them reshaped rather than left
  # invisible. A migration that loses the marker silently stops being re-run.
  run grep -c '^# adoption-sensitive:' "$MIGRATION"
  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
}
