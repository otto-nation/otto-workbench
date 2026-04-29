#!/usr/bin/env bats
# Tests for the migration framework (lib/migrations.sh) and validator (bin/local/validate-migrations).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  VALIDATOR="$REPO_ROOT/bin/local/validate-migrations"
  TMPDIR="$(mktemp -d)"

  # Build a minimal fake workbench with ui.sh stubs and constants
  FAKE_ROOT="$TMPDIR/workbench"
  FAKE_STATE="$TMPDIR/state"
  mkdir -p "$FAKE_ROOT/lib" "$FAKE_STATE"

  cat > "$FAKE_ROOT/lib/ui.sh" <<'STUB'
#!/usr/bin/env bash
WORKBENCH_DIR="${WORKBENCH_DIR}"
BOLD='' GREEN='' BLUE='' YELLOW='' RED='' CYAN='' DIM='' NC=''
info()    { echo "→ $*"; }
success() { echo "✓ $*"; }
warn()    { echo "⚠ $*"; }
err()     { echo "✗ $*" >&2; }
apply_config_patch() { :; }
STUB
  # Inject the actual WORKBENCH_DIR into the stub
  sed -i.bak "s|WORKBENCH_DIR=\"\${WORKBENCH_DIR}\"|WORKBENCH_DIR=\"$FAKE_ROOT\"|" "$FAKE_ROOT/lib/ui.sh" && rm -f "$FAKE_ROOT/lib/ui.sh.bak"

  cat > "$FAKE_ROOT/lib/constants.sh" <<CONST
#!/usr/bin/env bash
WORKBENCH_DIR="$FAKE_ROOT"
WORKBENCH_STATE_DIR="$FAKE_STATE"
MIGRATIONS_STATE_FILE="$FAKE_STATE/migrations.applied"
CONST

  # Source the real component discovery and migrations libraries with our fake paths
  cp "$REPO_ROOT/lib/components.sh" "$FAKE_ROOT/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$FAKE_ROOT/lib/migrations.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a valid migration file in the fake workbench
create_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
${fn_name}() {
  :
}
EOF
}

# Helper: source the framework and run all migrations
run_migrations_in_fake() {
  (
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    run_all_migrations
  )
}

# ─── Component discovery under set -e ────────────────────────────────────────

@test "discover_migration_dirs returns 0 under set -e with no migrations" {
  # Regression: glob non-match caused [[ -d ... ]] to return 1, killing set -e scripts
  run bash -c "
    set -e
    . '$FAKE_ROOT/lib/ui.sh'
    . '$FAKE_ROOT/lib/constants.sh'
    . '$FAKE_ROOT/lib/components.sh'
    dirs=()
    discover_migration_dirs dirs
    echo \"count=\${#dirs[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"count=0"* ]]
}

@test "discover_step_files returns 0 under set -e with no steps" {
  run bash -c "
    set -e
    . '$FAKE_ROOT/lib/ui.sh'
    . '$FAKE_ROOT/lib/constants.sh'
    . '$FAKE_ROOT/lib/components.sh'
    files=()
    discover_step_files files
    echo \"count=\${#files[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"count=0"* ]]
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
  create_migration "mycomp" "20250101-test.sh" "migration_20250101_test"

  # Pre-populate state file
  mkdir -p "$FAKE_STATE"
  echo "mycomp/20250101-test.sh" > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"already applied"* ]]
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

# ─── Duplicate filename detection ───────────────────────────────────────────

@test "validator detects duplicate filenames across components" {
  create_migration "comp1" "20250101-dupe.sh" "migration_20250101_dupe"
  create_migration "comp2" "20250101-dupe.sh" "migration_20250101_dupe"

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"duplicate migration filename"* ]]
}
