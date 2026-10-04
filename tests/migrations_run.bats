#!/usr/bin/env bats
# Tests for lib/migrations.sh — execution, ordering, pruning, failure isolation — and the validator against the framework.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'migrations_helper'
  common_setup
  VALIDATOR="$REPO_ROOT/bin/local/validate-migrations"
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

# ─── Smoke test: validator passes against the real repo ──────────────────────

@test "validate-migrations passes against the current repo" {
  run bash "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ─── Validator: filename format ──────────────────────────────────────────────

@test "validator rejects bad filename format" {
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/bad-name.sh" <<'EOF'
#!/usr/bin/env bash
migration_bad_name() { :; }
EOF

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"filename must match YYYYMMDD-slug.sh"* ]]
}

@test "validator accepts valid filename format" {
  create_migration "mycomp" "20250101-test-migration.sh" "migration_20250101_test_migration"

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ─── Validator: function naming ──────────────────────────────────────────────

@test "validator rejects missing function" {
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-test.sh" <<'EOF'
#!/usr/bin/env bash
wrong_function_name() { :; }
EOF

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"expected function migration_20250101_test() not found"* ]]
}

# ─── Validator: shebang ─────────────────────────────────────────────────────

@test "validator rejects missing shebang" {
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-test.sh" <<'EOF'
# no shebang
migration_20250101_test() { :; }
EOF

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing #!/usr/bin/env bash shebang"* ]]
}

# ─── Migration execution: runs and records ───────────────────────────────────

@test "migration runs and records in state file" {
  create_migration "mycomp" "20250101-test.sh" "migration_20250101_test"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  # State file should contain the entry
  [ -f "$FAKE_STATE/migrations.applied" ]
  grep -qxF "mycomp/20250101-test.sh" "$FAKE_STATE/migrations.applied"
}

# ─── Migration execution: skips already applied ─────────────────────────────

@test "migration skips already-applied entries" {
  # Migration body writes a marker — if it runs again, we'll see a second line
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-test.sh" <<EOF
#!/usr/bin/env bash
migration_20250101_test() {
  echo "EXECUTED" >> "$TMPDIR/exec.log"
}
EOF

  # Pre-populate state file
  mkdir -p "$FAKE_STATE"
  echo "mycomp/20250101-test.sh" > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  # Migration function must not have been called
  [ ! -f "$TMPDIR/exec.log" ]
}

# ─── Migration execution: ordering ──────────────────────────────────────────

@test "migrations run in chronological order" {
  # Create two migrations — the function bodies write to a log to verify order
  mkdir -p "$FAKE_ROOT/mycomp/migrations"

  local log_file="$TMPDIR/order.log"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-first.sh" <<EOF
#!/usr/bin/env bash
migration_20250101_first() {
  echo "first" >> "$log_file"
}
EOF
  cat > "$FAKE_ROOT/mycomp/migrations/20250201-second.sh" <<EOF
#!/usr/bin/env bash
migration_20250201_second() {
  echo "second" >> "$log_file"
}
EOF

  run_migrations_in_fake

  [ "$(sed -n '1p' "$log_file")" = "first" ]
  [ "$(sed -n '2p' "$log_file")" = "second" ]
}

# ─── Stale state pruning ────────────────────────────────────────────────────

@test "stale state entries are pruned" {
  create_migration "mycomp" "20250101-test.sh" "migration_20250101_test"

  # Pre-populate state with a stale entry and a valid one
  mkdir -p "$FAKE_STATE"
  printf '%s\n' "mycomp/20250101-test.sh" "old/20240101-removed.sh" > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pruned stale migration state"* ]]

  # Stale entry should be gone, valid one should remain
  run ! grep -qxF "old/20240101-removed.sh" "$FAKE_STATE/migrations.applied"
  grep -qxF "mycomp/20250101-test.sh" "$FAKE_STATE/migrations.applied"
}

# ─── No migrations found ────────────────────────────────────────────────────

@test "handles no migrations gracefully" {
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"no migrations found"* ]]
}

# ─── Failure isolation ───────────────────────────────────────────────────────

# Helper: create a migration whose function returns non-zero, under the `set -e`
# real migration files carry.
create_failing_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
${fn_name}() {
  return 1
}
EOF
}

@test "a failing migration warns, is not recorded, and the sync keeps going" {
  create_failing_migration "comp1" "20250101-fails.sh" "migration_20250101_fails"
  create_migration "comp2" "20250201-later.sh" "migration_20250201_later"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration failed: 20250101-fails.sh"* ]]
  [[ "$output" == *"will retry on next run"* ]]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  run ! grep -qxF "comp1/20250101-fails.sh" "$FAKE_STATE/migrations.applied"
  grep -qxF "comp2/20250201-later.sh" "$FAKE_STATE/migrations.applied"
}

@test "a self-invoking migration cannot abort the run on the sourcing pass" {
  # The framework sources the file and then calls the function. A file that
  # also calls itself runs on the sourcing pass too, where — under its own
  # `set -e`, and outside the `if` that turns a failure into warn-and-retry —
  # a non-zero return used to exit the whole sync. validate-migrations
  # rejects the shape now, but the framework has to hold for a file the
  # validator never saw.
  mkdir -p "$FAKE_ROOT/comp1/migrations"
  cat > "$FAKE_ROOT/comp1/migrations/20250101-selfcall.sh" <<'EOF'
#!/usr/bin/env bash
set -e
migration_20250101_selfcall() {
  return 1
}
migration_20250101_selfcall
EOF
  create_migration "comp2" "20250201-later.sh" "migration_20250201_later"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"could not be loaded"* ]]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  run ! grep -qxF "comp1/20250101-selfcall.sh" "$FAKE_STATE/migrations.applied"
  grep -qxF "comp2/20250201-later.sh" "$FAKE_STATE/migrations.applied"
}

@test "sourcing a migration does not arm errexit for the rest of the sync" {
  # A sourced `set -e` outlives the source. Left in place it would put every
  # component that syncs after the migrations under errexit, which nothing
  # downstream expects — so the framework restores the caller's own setting.
  mkdir -p "$FAKE_ROOT/comp1/migrations"
  cat > "$FAKE_ROOT/comp1/migrations/20250101-armed.sh" <<'EOF'
#!/usr/bin/env bash
set -e
migration_20250101_armed() {
  :
}
EOF

  run bash -c "
    . '$FAKE_ROOT/lib/ui.sh'
    . '$FAKE_ROOT/lib/constants.sh'
    . '$FAKE_ROOT/lib/migrations.sh'
    run_all_migrations > /dev/null
    case \$- in *e*) echo 'ERREXIT ARMED' ;; *) echo 'ERREXIT CLEAR' ;; esac
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ERREXIT CLEAR"* ]]
}

# Helper: a migration whose file scope fails before the definition it exists for.
# The definition succeeds, so the source's own exit status is 0 and says nothing
# about the failure — the framework has to look somewhere else to see it.
create_scope_failing_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
_precondition() {
  return 1
}
_precondition
${fn_name}() {
  touch "$FAKE_ROOT/${fn_name}.ran"
}
EOF
}

@test "a file-scope failure before a good definition is a load failure" {
  create_scope_failing_migration "comp1" "20250101-scoped.sh" "migration_20250101_scoped"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"could not be loaded"* ]]

  [ ! -e "$FAKE_ROOT/migration_20250101_scoped.ran" ]
  run ! grep -qxF "comp1/20250101-scoped.sh" "$FAKE_STATE/migrations.applied"
}

@test "a file that fails to load stops neither the next migration nor the next component" {
  create_scope_failing_migration "comp1" "20250101-scoped.sh" "migration_20250101_scoped"
  create_migration "comp1" "20250102-sibling.sh" "migration_20250102_sibling"
  create_migration "comp2" "20250201-later.sh" "migration_20250201_later"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  grep -qxF "comp1/20250102-sibling.sh" "$FAKE_STATE/migrations.applied"
  grep -qxF "comp2/20250201-later.sh" "$FAKE_STATE/migrations.applied"
  run ! grep -qxF "comp1/20250101-scoped.sh" "$FAKE_STATE/migrations.applied"
}

@test "a clean migration still loads and applies alongside one that cannot" {
  create_scope_failing_migration "comp1" "20250101-scoped.sh" "migration_20250101_scoped"
  mkdir -p "$FAKE_ROOT/comp2/migrations"
  cat > "$FAKE_ROOT/comp2/migrations/20250201-good.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250201_good() {
  touch "$FAKE_ROOT/good.ran"
}
EOF

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250201-good.sh"* ]]

  [ -e "$FAKE_ROOT/good.ran" ]
  grep -qxF "comp2/20250201-good.sh" "$FAKE_STATE/migrations.applied"
}

@test "the caller's errexit setting survives a migration that fails to load" {
  # Both directions matter: the sync must not come back armed when it started
  # clear, and must not come back disarmed when its caller relies on errexit.
  create_scope_failing_migration "comp1" "20250101-scoped.sh" "migration_20250101_scoped"

  run bash -c "
    . '$FAKE_ROOT/lib/ui.sh'
    . '$FAKE_ROOT/lib/constants.sh'
    . '$FAKE_ROOT/lib/migrations.sh'
    set -e
    run_all_migrations > /dev/null
    case \$- in *e*) echo 'ARMED-STAYED-ARMED' ;; *) echo 'ARMED-WENT-CLEAR' ;; esac
    set +e
    run_all_migrations > /dev/null
    case \$- in *e*) echo 'CLEAR-WENT-ARMED' ;; *) echo 'CLEAR-STAYED-CLEAR' ;; esac
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ARMED-STAYED-ARMED"* ]]
  [[ "$output" == *"CLEAR-STAYED-CLEAR"* ]]
}

# ─── Duplicate filename detection ───────────────────────────────────────────

@test "validator detects duplicate filenames across components" {
  create_migration "comp1" "20250101-dupe.sh" "migration_20250101_dupe"
  create_migration "comp2" "20250101-dupe.sh" "migration_20250101_dupe"

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"duplicate migration filename"* ]]
}
