#!/usr/bin/env bats
# Tests for lib/migrations.sh — checkout-scoped migrations and migrations that find nothing to do.
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

# ─── Checkout-scoped migrations ──────────────────────────────────────────────
#
# A migration that edits files inside a repo is done per repo, not per machine.
# The marker in its header hands the loop over the registry to the framework,
# which records one state line per repo it visited — so a repo the machine
# learns about later is a key the state file simply does not hold yet.

@test "a checkout-scoped migration runs once per registered repo" {
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (2 projects)"* ]]

  run cat "$TMPDIR/exec.log"
  [ "${lines[0]}" = "$TMPDIR/repo-a" ]
  [ "${lines[1]}" = "$TMPDIR/repo-b" ]

  # One line per repo, and no bare key: a bare key is the machine claiming to be
  # done, which is the whole thing a checkout-scoped migration cannot say.
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
  run ! grep -qxF "mycomp/20250101-proj.sh" "$FAKE_STATE/migrations.applied"
}

@test "a checkout-scoped migration on a machine with no repos records nothing" {
  # No repo to visit is not the migration being done — it is a machine that has
  # nothing to apply it to yet. Recording anything here is what would leave the
  # first repo registered afterwards unvisited, so the run is silent and the
  # state file stays empty until there is a repo to name.
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
  [ ! -e "$TMPDIR/exec.log" ]

  register_fake_project "$TMPDIR/repo-a"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (1 project)"* ]]
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
}

@test "a repo that registers after the first sync is visited on the next" {
  # The bug this whole shape exists for: under a single machine-wide key the
  # first sync recorded the migration as done and repo-b never received it.
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  run_migrations_in_fake

  register_fake_project "$TMPDIR/repo-b"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (1 project)"* ]]

  # repo-a exactly once — a late registration re-runs the migration for the repo
  # that is missing, not for the ones already recorded.
  run cat "$TMPDIR/exec.log"
  [ "${#lines[@]}" -eq 2 ]
  [ "${lines[0]}" = "$TMPDIR/repo-a" ]
  [ "${lines[1]}" = "$TMPDIR/repo-b" ]
}

@test "a per-repo entry survives the pruning every sync runs" {
  # Prune reads the same lines back on the next sync. An entry it did not
  # recognise would be dropped with a warning, and the migration would run
  # everywhere again — silently, since it has to be idempotent anyway.
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  run_migrations_in_fake

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pruned stale migration state"* ]]

  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 1 ]
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
}

@test "entries naming a repo that left the registry are dropped" {
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  run_migrations_in_fake

  rm -rf "$TMPDIR/repo-b"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  # Silently. A checkout is removed by `wt remove` as a matter of routine, and
  # the bookkeeping following it out is not news — the operator reading a sync
  # log got a count of forgotten entries every time and could do nothing with it.
  [[ "$output" != *"no longer registered"* ]]
  [[ "$output" != *"Forgot"* ]]

  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  run ! grep -qF "repo-b" "$FAKE_STATE/migrations.applied"
}

@test "a bare entry for a migration that became checkout-scoped is dropped" {
  # How a migration converts scope: the machine-wide line the old shape recorded
  # says nothing about any repo, so prune drops it and every registered repo is
  # visited. Re-dating the file to force that is not needed.
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  echo "mycomp/20250101-proj.sh" > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  # Not stale — the file is still there, it just records itself differently now.
  [[ "$output" != *"Pruned stale migration state"* ]]

  [ "$(cat "$TMPDIR/exec.log")" = "$TMPDIR/repo-a" ]
  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-proj.sh"$'\t'"$TMPDIR/repo-a" ]
}

@test "a per-repo entry for a migration that lost the marker is dropped" {
  # The other direction, and why prune checks both: a per-repo line means
  # nothing to a migration that runs once, and no line the framework writes
  # would ever match it — so the machine-wide run it is owed would never happen.
  create_migration mycomp 20250101-plain.sh migration_20250101_plain
  register_fake_project "$TMPDIR/repo-a"
  printf 'mycomp/20250101-plain.sh\t%s\n' "$TMPDIR/repo-a" > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-plain.sh" ]
}

# Helper: a checkout-scoped migration that fails for any repo holding a `fail`
# file, and records the rest.
create_failing_checkout_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
# checkout-scoped: edits files inside each repo.
${fn_name}() {
  if [[ -e "\$1/fail" ]]; then
    return 1
  fi
  echo "\$1" >> "$TMPDIR/exec.log"
}
EOF
}

@test "a repo whose run fails is the only one retried" {
  # Per-repo state is per-repo retry too: the repos that succeeded are recorded
  # and the next sync leaves them alone.
  create_failing_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  touch "$TMPDIR/repo-b/fail"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration failed: 20250101-proj.sh in $TMPDIR/repo-b"* ]]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  run ! grep -qF "repo-b" "$FAKE_STATE/migrations.applied"

  rm "$TMPDIR/repo-b/fail"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run cat "$TMPDIR/exec.log"
  [ "${#lines[@]}" -eq 2 ]
  [ "${lines[0]}" = "$TMPDIR/repo-a" ]
  [ "${lines[1]}" = "$TMPDIR/repo-b" ]
}

@test "forgetting an adoption-sensitive migration drops its per-repo entries" {
  # Both state rewriters compare against a discovered key, which no per-repo
  # line equals — comparing whole lines would leave a marked migration recorded
  # for exactly the repos it had been applied to.
  mkdir -p "$FAKE_ROOT/mycomp/migrations" "$FAKE_LEGACY"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-both.sh" <<EOF
#!/usr/bin/env bash
# adoption-sensitive: drains a path adoption writes into.
# checkout-scoped: drains it inside each repo.
migration_20250101_both() {
  echo "\$1" >> "$TMPDIR/exec.log"
}
EOF
  printf 'mycomp/20250101-both.sh\t%s\n' "$TMPDIR/repo-a" "$TMPDIR/repo-b" \
    > "$FAKE_LEGACY/migrations.applied"

  run adopt_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"will run again"* ]]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
}

@test "this repo's checkout-scoped migrations are discovered by their real keys" {
  # Against the real tree, for the reason the adoption-sensitive twin above is:
  # a typo in the marker line is invisible everywhere else, and the migration
  # would quietly go back to claiming the whole machine after one run.
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    keys=()
    _discover_migration_keys keys \"\$_CHECKOUT_SCOPED_MARKER\"
    printf '%s\n' \"\${keys[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ai/claude/20260819-context-to-architecture.sh"* ]]
}

# ─── Migrations that find nothing to do ──────────────────────────────────────
#
# A migration has to be idempotent, so "already in the target shape" is its
# commonest outcome and, for a checkout-scoped one on a machine that registers a
# worktree whenever one is opened, very nearly its only outcome. Returning 0
# there reported a rename that could never happen as work applied, once per
# sync, forever. MIGRATION_NOOP is how a migration says which of the two it did.

# Helper: a checkout-scoped migration that answers MIGRATION_NOOP for any repo
# not holding a `work` file, and logs every repo it is handed either way.
create_noop_checkout_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
# checkout-scoped: edits files inside each repo.
${fn_name}() {
  echo "\$1" >> "$TMPDIR/exec.log"
  if [[ ! -e "\$1/work" ]]; then
    return "\$MIGRATION_NOOP"
  fi
}
EOF
}

@test "a migration that finds nothing to do is recorded but not announced" {
  # And the sync survives it: MIGRATION_NOOP is a non-zero status returned into
  # a caller running under errexit, which is what SYNC CONTINUED proves.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-noop.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250101_noop() {
  echo "CALLED" >> "$TMPDIR/exec.log"
  return "\$MIGRATION_NOOP"
}
EOF

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"SYNC CONTINUED"* ]]
  [[ "$output" != *"Migration applied"* ]]
  # Not a failure either — a warning here would send an operator looking for a
  # broken migration that did exactly what it was supposed to.
  [[ "$output" != *"Migration failed"* ]]
  # The summary line only prints when something applied, so a sync whose whole
  # migration story is "nothing to do" prints nothing about migrations at all.
  [[ "$output" != *"already applied"* ]]

  [ "$(cat "$TMPDIR/exec.log")" = "CALLED" ]
  grep -qxF "mycomp/20250101-noop.sh" "$FAKE_STATE/migrations.applied"
}

@test "a migration that found nothing to do is not visited again" {
  # Recorded, not retried: the target has been looked at and the answer cannot
  # change, so running it every sync forever is the one thing this must not do.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-noop.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250101_noop() {
  echo "CALLED" >> "$TMPDIR/exec.log"
  return "\$MIGRATION_NOOP"
}
EOF

  run_migrations_in_fake
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 1 ]
}

@test "the project count names the repos changed, not the repos visited" {
  create_noop_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  register_fake_project "$TMPDIR/repo-c"
  touch "$TMPDIR/repo-b/work"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (1 project)"* ]]

  # All three were visited, and all three are recorded — the two that found
  # nothing to do are as done as the one that did the work.
  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 3 ]
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-c"
}

@test "a sync whose only new repo has nothing to do says nothing" {
  # The shape that put a line in every sync: a worktree registers itself the
  # first time anything runs in it, the framework visits it because the state
  # file does not name it yet, and the visit finds the work already done.
  create_noop_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  touch "$TMPDIR/repo-a/work"
  run_migrations_in_fake

  register_fake_project "$TMPDIR/repo-b"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
}

@test "a repo that fails is still a failure alongside one that no-ops" {
  # MIGRATION_NOOP is one specific status, not "any non-zero is fine now".
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# checkout-scoped: edits files inside each repo.
migration_20250101_proj() {
  if [[ -e "\$1/fail" ]]; then
    return 1
  fi
  return "\$MIGRATION_NOOP"
}
EOF
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  touch "$TMPDIR/repo-b/fail"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration failed: 20250101-proj.sh in $TMPDIR/repo-b"* ]]
  [[ "$output" != *"Migration applied"* ]]

  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  run ! grep -qF "repo-b" "$FAKE_STATE/migrations.applied"
}

@test "changed, unchanged and failed repos are each reported as themselves" {
  # All three outcomes in one run, because the count, the warning and the state
  # file each read a different one and only a mixed run can tell them apart.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# checkout-scoped: edits files inside each repo.
migration_20250101_proj() {
  if [[ -e "\$1/fail" ]]; then
    return 1
  fi
  if [[ -e "\$1/work" ]]; then
    return 0
  fi
  return "\$MIGRATION_NOOP"
}
EOF
  register_fake_project "$TMPDIR/repo-work"
  register_fake_project "$TMPDIR/repo-noop"
  register_fake_project "$TMPDIR/repo-fail"
  touch "$TMPDIR/repo-work/work" "$TMPDIR/repo-fail/fail"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  # One repo changed, so the migration is announced — and named once, not three
  # times, since neither the no-op nor the failure is work it did.
  [[ "$output" == *"Migration applied: 20250101-proj.sh (1 project)"* ]]
  [[ "$output" == *"Migration failed: 20250101-proj.sh in $TMPDIR/repo-fail"* ]]
  [[ "$output" == *"migrations: 1 applied, 0 already applied"* ]]

  # Recorded: the one that worked and the one that had nothing to do. Not the
  # one that failed — that repo alone is retried next sync.
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-work"
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-noop"
  run ! grep -qF "repo-fail" "$FAKE_STATE/migrations.applied"
}

@test "the context rename counts only the repos it renamed in" {
  # Against the real migration file, because the whole point is what this one
  # prints on a machine full of worktrees cut from a main that already holds
  # architecture.md.
  mkdir -p "$FAKE_ROOT/ai/claude/migrations"
  cp "$REPO_ROOT/ai/claude/migrations/20260819-context-to-architecture.sh" \
    "$FAKE_ROOT/ai/claude/migrations/"
  register_fake_project "$TMPDIR/repo-old"
  register_fake_project "$TMPDIR/repo-new"
  register_fake_project "$TMPDIR/repo-bare"
  mkdir -p "$TMPDIR/repo-old/.claude" "$TMPDIR/repo-new/.claude"
  echo old > "$TMPDIR/repo-old/.claude/context.md"
  echo new > "$TMPDIR/repo-new/.claude/architecture.md"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"(1 project)"* ]]
  [[ "$output" != *"(3 projects)"* ]]

  [ "$(cat "$TMPDIR/repo-old/.claude/architecture.md")" = "old" ]
  [ ! -e "$TMPDIR/repo-old/.claude/context.md" ]
  # Left alone, not overwritten with the file it already had.
  [ "$(cat "$TMPDIR/repo-new/.claude/architecture.md")" = "new" ]
}
