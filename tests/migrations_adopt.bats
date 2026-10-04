#!/usr/bin/env bats
# Tests for lib/migrations.sh — adopting the legacy workbench root, and migrations sensitive to it.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'migrations_helper'
  common_setup
  _migrations_fake_workbench

  # Source the real component discovery and migrations libraries with our fake paths
  cp "$REPO_ROOT/lib/components.sh" "$FAKE_ROOT/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$FAKE_ROOT/lib/migrations.sh"
  cp "$REPO_ROOT/lib/projects.sh" "$FAKE_ROOT/lib/projects.sh"
  cp "$REPO_ROOT/lib/gitenv.sh" "$FAKE_ROOT/lib/gitenv.sh"
  cp "$REPO_ROOT/lib/git_layout.sh" "$FAKE_ROOT/lib/git_layout.sh"
  # _project_rewrite reads a file's mode through portable.sh, and
  # record_project_repo_ids is the first thing on the sync path to call it.
  cp "$REPO_ROOT/lib/portable.sh" "$FAKE_ROOT/lib/portable.sh"
}

teardown() {
  common_teardown
}

# ─── Legacy root adoption ────────────────────────────────────────────────────

@test "adoption is a no-op when the legacy root does not exist" {
  run adopt_in_fake
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "adoption sorts entries between the state and config roots" {
  mkdir -p "$FAKE_LEGACY/reviews/repo-42" "$FAKE_LEGACY/overrides/ai"
  echo "applied" > "$FAKE_LEGACY/migrations.applied"
  echo "ultra" > "$FAKE_LEGACY/reuse-level"
  echo "review" > "$FAKE_LEGACY/reviews/repo-42/review.md"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ "$(cat "$FAKE_STATE/migrations.applied")" = "applied" ]
  [ "$(cat "$FAKE_STATE/reviews/repo-42/review.md")" = "review" ]
  [ "$(cat "$FAKE_CONFIG/reuse-level")" = "ultra" ]
  [ -d "$FAKE_CONFIG/overrides/ai" ]
  [ ! -d "$FAKE_LEGACY" ]
}

@test "adoption sends every name in _LEGACY_CONFIG_ENTRIES to the config root" {
  # The list is the whole classification: anything dropped from it silently
  # becomes state, so each entry needs its own evidence.
  # Every entry is written as a plain file here, including `overrides`
  # (a directory in real usage) — this test only exercises the name-based
  # classification, not the directory-merge path, which "adoption sorts
  # entries between the state and config roots" above covers.
  local names entry
  names=$(
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    printf '%s\n' "${_LEGACY_CONFIG_ENTRIES[@]}"
  )
  [ -n "$names" ]

  mkdir -p "$FAKE_LEGACY"
  while IFS= read -r entry; do
    echo "$entry" > "$FAKE_LEGACY/$entry"
  done <<< "$names"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  while IFS= read -r entry; do
    [ "$(cat "$FAKE_CONFIG/$entry")" = "$entry" ]
    [ ! -e "$FAKE_STATE/$entry" ]
  done <<< "$names"
}

@test "adoption leaves behind an entry no root claims" {
  # <state>/logs/ is deleted on purpose. Adoption runs before any migration
  # reads its bookkeeping, so carrying logs/ across would reinstate a directory
  # the migration that removed it is already recorded as applied for, and that
  # migration will never run again to take it back out.
  mkdir -p "$FAKE_LEGACY/logs/dream-scan"
  printf '{"ts":"2026-01-01T00:00:00Z"}\n' > "$FAKE_LEGACY/logs/dream-scan/trail.jsonl"
  echo "applied" > "$FAKE_LEGACY/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"belong to no root"* ]]

  [ ! -e "$FAKE_STATE/logs" ]
  [ -f "$FAKE_LEGACY/logs/dream-scan/trail.jsonl" ]
  # An unclaimed entry is skipped, not a reason to stop: everything a root
  # does own still moves in the same pass.
  [ "$(cat "$FAKE_STATE/migrations.applied")" = "applied" ]
}

@test "adoption skips every name in _LEGACY_UNCLAIMED_ENTRIES" {
  # Mirrors the config-entry test above: the list is the whole classification
  # on its side, so anything dropped from it silently becomes state again.
  local names entry
  names=$(
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    printf '%s\n' "${_LEGACY_UNCLAIMED_ENTRIES[@]}"
  )
  [ -n "$names" ]

  mkdir -p "$FAKE_LEGACY"
  while IFS= read -r entry; do
    echo "$entry" > "$FAKE_LEGACY/$entry"
  done <<< "$names"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  while IFS= read -r entry; do
    [ "$(cat "$FAKE_LEGACY/$entry")" = "$entry" ]
    [ ! -e "$FAKE_STATE/$entry" ]
    [ ! -e "$FAKE_CONFIG/$entry" ]
  done <<< "$names"
}

@test "skipping an unclaimed entry is idempotent" {
  # The legacy root survives while it still holds one, so adoption keeps
  # running — it has to reach the same decision every time.
  mkdir -p "$FAKE_LEGACY/logs"
  echo "leftover" > "$FAKE_LEGACY/logs/dream-scan.jsonl"

  adopt_in_fake
  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"belong to no root"* ]]
  [ "$(cat "$FAKE_LEGACY/logs/dream-scan.jsonl")" = "leftover" ]
  [ ! -e "$FAKE_STATE/logs" ]
}

@test "adoption is idempotent across repeated runs" {
  mkdir -p "$FAKE_LEGACY"
  echo "applied" > "$FAKE_LEGACY/migrations.applied"

  adopt_in_fake
  run adopt_in_fake

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ "$(cat "$FAKE_STATE/migrations.applied")" = "applied" ]
}

@test "adoption resumes a run that was interrupted partway through a directory" {
  mkdir -p "$FAKE_LEGACY/reviews/second" "$FAKE_STATE/reviews/first"
  echo "already there" > "$FAKE_STATE/reviews/first/review.md"
  echo "left behind" > "$FAKE_LEGACY/reviews/second/review.md"
  echo "hidden" > "$FAKE_LEGACY/reviews/.index"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ "$(cat "$FAKE_STATE/reviews/first/review.md")" = "already there" ]
  [ "$(cat "$FAKE_STATE/reviews/second/review.md")" = "left behind" ]
  [ "$(cat "$FAKE_STATE/reviews/.index")" = "hidden" ]
  [ ! -d "$FAKE_LEGACY" ]
}

@test "adoption keeps both copies when a file exists on each side and reports the leftover" {
  mkdir -p "$FAKE_LEGACY" "$FAKE_STATE"
  echo "old" > "$FAKE_LEGACY/migrations.applied"
  echo "new" > "$FAKE_STATE/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"kept the new one"* ]]
  [[ "$output" == *"could not be adopted"* ]]

  [ "$(cat "$FAKE_STATE/migrations.applied")" = "new" ]
  [ "$(cat "$FAKE_LEGACY/migrations.applied")" = "old" ]
}

@test "adoption merges a trail both roots hold rather than keeping both" {
  # One history in two files: keeping both would hide the older from otto-log,
  # which globs for the exact name. Staged under reviews/, not the logs/ this
  # used to use — logs/ belongs to no root any more, so adoption skips it.
  mkdir -p "$FAKE_LEGACY/reviews/repo-42" "$FAKE_STATE/reviews/repo-42"
  printf '{"ts":"2026-01-01T00:00:00Z","n":1}\n' > "$FAKE_LEGACY/reviews/repo-42/trail.jsonl"
  printf '{"ts":"2026-08-01T00:00:00Z","n":2}\n' > "$FAKE_STATE/reviews/repo-42/trail.jsonl"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"kept the new one"* ]]

  [ "$(wc -l < "$FAKE_STATE/reviews/repo-42/trail.jsonl")" -eq 2 ]
  grep -q '"n":1' "$FAKE_STATE/reviews/repo-42/trail.jsonl"
  grep -q '"n":2' "$FAKE_STATE/reviews/repo-42/trail.jsonl"
  [ ! -d "$FAKE_LEGACY" ]
}

@test "merging a trail onto a file with no trailing newline keeps both records whole" {
  mkdir -p "$FAKE_LEGACY/reviews/repo-42" "$FAKE_STATE/reviews/repo-42"
  printf '{"ts":"2026-01-01T00:00:00Z","n":1}\n' > "$FAKE_LEGACY/reviews/repo-42/trail.jsonl"
  printf '{"ts":"2026-08-01T00:00:00Z","n":2}' > "$FAKE_STATE/reviews/repo-42/trail.jsonl"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ "$(wc -l < "$FAKE_STATE/reviews/repo-42/trail.jsonl")" -eq 2 ]
  grep -qx '{"ts":"2026-08-01T00:00:00Z","n":2}' "$FAKE_STATE/reviews/repo-42/trail.jsonl"
  grep -qx '{"ts":"2026-01-01T00:00:00Z","n":1}' "$FAKE_STATE/reviews/repo-42/trail.jsonl"
}

@test "adoption merges a monthly usage ledger" {
  mkdir -p "$FAKE_LEGACY/usage" "$FAKE_STATE/usage"
  printf '{"ts":"2026-08-01T00:00:00Z"}\n' > "$FAKE_LEGACY/usage/2026-08.jsonl"
  printf '{"ts":"2026-08-02T00:00:00Z"}\n' > "$FAKE_STATE/usage/2026-08.jsonl"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$FAKE_STATE/usage/2026-08.jsonl")" -eq 2 ]
}

@test "adoption keeps both review session logs rather than splicing two runs" {
  # session.jsonl is a whole-file write whose convention is prior-content-first
  # (agent_retry.restore_preserved) — concatenating would misreport both runs.
  mkdir -p "$FAKE_LEGACY/reviews/run" "$FAKE_STATE/reviews/run"
  echo "old" > "$FAKE_LEGACY/reviews/run/session.jsonl"
  echo "new" > "$FAKE_STATE/reviews/run/session.jsonl"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"kept the new one"* ]]

  [ "$(cat "$FAKE_STATE/reviews/run/session.jsonl")" = "new" ]
  [ "$(cat "$FAKE_LEGACY/reviews/run/session.jsonl")" = "old" ]
}

@test "adoption leaves the legacy root alone when a root still resolves to it" {
  # A machine that pins WORKBENCH_STATE_DIR to the old path: there is nowhere
  # to move the state to, and moving a directory into itself would destroy it.
  cat >> "$FAKE_ROOT/lib/constants.sh" <<CONST
WORKBENCH_STATE_DIR="$FAKE_LEGACY"
CONST
  mkdir -p "$FAKE_LEGACY"
  echo "applied" > "$FAKE_LEGACY/migrations.applied"
  echo "ultra" > "$FAKE_LEGACY/reuse-level"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ "$(cat "$FAKE_LEGACY/migrations.applied")" = "applied" ]
  [ "$(cat "$FAKE_CONFIG/reuse-level")" = "ultra" ]
}

@test "adoption moves the docker aliases symlink without following it" {
  # The target is deliberately absent: the symlink is written before the
  # runtime's aliases file exists on a fresh machine, and a mover that tested
  # only -e would leave it behind.
  mkdir -p "$FAKE_LEGACY"
  ln -s "$TMPDIR/colima/aliases.zsh" "$FAKE_LEGACY/docker-aliases.zsh"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ -L "$FAKE_STATE/docker-aliases.zsh" ]
  [ "$(readlink "$FAKE_STATE/docker-aliases.zsh")" = "$TMPDIR/colima/aliases.zsh" ]
}

@test "adoption runs before the framework reads its own state file" {
  # migrations.applied is one of the files being moved. If the framework read
  # the state root first, it would see an empty file and re-run every
  # historical migration.
  mkdir -p "$FAKE_ROOT/mycomp/migrations" "$FAKE_LEGACY"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-test.sh" <<EOF
#!/usr/bin/env bash
migration_20250101_test() {
  echo "EXECUTED" >> "$TMPDIR/exec.log"
}
EOF
  echo "mycomp/20250101-test.sh" > "$FAKE_LEGACY/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [ ! -f "$TMPDIR/exec.log" ]
  grep -qxF "mycomp/20250101-test.sh" "$FAKE_STATE/migrations.applied"
}

# ─── Adoption-sensitive migrations ───────────────────────────────────────────
#
# A migration that drains a path adoption writes into is undone by an adoption
# that runs after it is recorded as applied. The marker in the migration's own
# header is what buys it another pass.

# Helper: create a migration that declares itself adoption-sensitive and
# appends its own name to $TMPDIR/exec.log when it runs.
create_sensitive_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
# adoption-sensitive: drains a path adoption writes into.
${fn_name}() {
  echo "$filename" >> "$TMPDIR/exec.log"
}
EOF
}

@test "adoption that moves nothing leaves the migration state alone" {
  # The reset is a cost — every marked migration runs again — so it may not fire
  # on the ordinary sync, which is every sync after the first.
  create_sensitive_migration mycomp 20250101-sensitive.sh migration_20250101_sensitive
  echo "mycomp/20250101-sensitive.sh" > "$FAKE_STATE/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  [ "$(cat "$FAKE_STATE/migrations.applied")" = "mycomp/20250101-sensitive.sh" ]
}

@test "a real adoption forgets the marked migrations and only those" {
  create_sensitive_migration mycomp 20250101-sensitive.sh migration_20250101_sensitive
  create_migration mycomp 20250102-plain.sh migration_20250102_plain
  mkdir -p "$FAKE_LEGACY"
  printf 'mycomp/20250101-sensitive.sh\nmycomp/20250102-plain.sh\n' \
    > "$FAKE_LEGACY/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"will run again"* ]]

  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250102-plain.sh" ]
}

@test "a migration with no marker keeps its state through an adoption" {
  # The counterpart to the test above, stated on its own: a blanket reset would
  # re-run a migration that removed something on purpose and put the removal
  # back, undoing an operator who deliberately restored it.
  create_migration mycomp 20250102-plain.sh migration_20250102_plain
  mkdir -p "$FAKE_LEGACY/reviews"
  echo "mycomp/20250102-plain.sh" > "$FAKE_LEGACY/migrations.applied"
  echo "data" > "$FAKE_LEGACY/reviews/x"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"will run again"* ]]

  [ "$(cat "$FAKE_STATE/migrations.applied")" = "mycomp/20250102-plain.sh" ]
}

@test "a marked migration runs again over the data adoption just moved" {
  # The end-to-end shape: the legacy root carries both the data and the
  # state file that says the migration which drains it is already done.
  create_sensitive_migration mycomp 20250101-sensitive.sh migration_20250101_sensitive
  create_migration mycomp 20250102-plain.sh migration_20250102_plain
  mkdir -p "$FAKE_LEGACY/reviews/repo-42"
  printf 'mycomp/20250101-sensitive.sh\nmycomp/20250102-plain.sh\n' \
    > "$FAKE_LEGACY/migrations.applied"
  echo "trail" > "$FAKE_LEGACY/reviews/repo-42/trail.jsonl"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  [ "$(cat "$TMPDIR/exec.log")" = "20250101-sensitive.sh" ]
  # Recorded again, so the sync after this one is back to skipping it.
  grep -qxF "mycomp/20250101-sensitive.sh" "$FAKE_STATE/migrations.applied"
  grep -qxF "mycomp/20250102-plain.sh" "$FAKE_STATE/migrations.applied"
}

@test "forgetting the marked migrations empties a state file that holds only them" {
  # printf over an empty array writes a blank line, which _prune_stale_migration_state
  # would then warn about as an unrecognised entry.
  create_sensitive_migration mycomp 20250101-sensitive.sh migration_20250101_sensitive
  mkdir -p "$FAKE_LEGACY"
  echo "mycomp/20250101-sensitive.sh" > "$FAKE_LEGACY/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
}

@test "forgetting keeps an unmarked entry on a state file's unterminated last line" {
  # `read` reports EOF for a final line with no newline after it, so a loop
  # without the `|| [[ -n "$line" ]]` guard never sees that entry and rewrites
  # the file without it — a migration silently marked un-applied.
  create_sensitive_migration mycomp 20250101-sensitive.sh migration_20250101_sensitive
  create_migration mycomp 20250102-plain.sh migration_20250102_plain
  mkdir -p "$FAKE_LEGACY"
  printf 'mycomp/20250101-sensitive.sh\nmycomp/20250102-plain.sh' \
    > "$FAKE_LEGACY/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]

  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250102-plain.sh" ]
}

@test "pruning keeps a live entry on a state file's unterminated last line" {
  # A stale first entry is what makes prune rewrite the file at all; the live
  # entry after it is unterminated, so an unguarded read never reaches it and
  # the rewrite leaves it out.
  create_migration mycomp 20250102-plain.sh migration_20250102_plain
  printf 'mycomp/20250199-gone.sh\nmycomp/20250102-plain.sh' \
    > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250102-plain.sh" ]
}

@test "this repo's adoption-sensitive migrations are discovered by their real keys" {
  # Against the real tree, not the fake one: the marker has to be spelled the
  # way lib/migrations.sh greps for it, and the state key has to match what
  # run_component_migrations records — a rename of the file breaks the second
  # even when the first still holds. Every marked migration is named, since a
  # typo in any one's marker line is invisible everywhere else.
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    keys=()
    _discover_migration_keys keys \"\$_ADOPTION_SENSITIVE_MARKER\"
    printf '%s\n' \"\${keys[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ai/claude/20260814-unify-trail-root.sh"* ]]
  [[ "$output" == *"bin/20260814-unify-workbench-config.sh"* ]]
  [[ "$output" == *"bin/20260824-lift-issue-tracker-key.sh"* ]]
}
