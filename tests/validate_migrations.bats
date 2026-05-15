#!/usr/bin/env bats
# Tests for validate-migrations — filename format, shebang, function naming,
# duplicate detection, and summary output.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_MIGRATIONS="$REPO_ROOT/bin/local/validate-migrations"

  # Build a fake workbench root the script can discover migration dirs from.
  # The script sources lib/ui.sh and lib/components.sh via relative paths from _SELF,
  # but WORKBENCH_DIR controls where it looks for migrations.
  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a migration file in a component's migrations/ dir
_make_migration() {
  local component="$1" filename="$2" func_name="${3:-}"
  local dir="$FAKE_WORKBENCH/$component/migrations"
  mkdir -p "$dir"

  if [[ -z "$func_name" ]]; then
    # Derive function name from filename: 20260417-slug.sh -> migration_20260417_slug
    func_name="migration_${filename%.sh}"
    func_name="${func_name//-/_}"
  fi

  cat > "$dir/$filename" <<EOF
#!/usr/bin/env bash
${func_name}() {
  echo "migrating"
}
EOF
}

# Helper: run validate-migrations with WORKBENCH_DIR overridden
_run_validate() {
  WORKBENCH_DIR="$FAKE_WORKBENCH" NO_COLOR=1 run "$VALIDATE_MIGRATIONS" "$@"
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "validate-migrations --help exits 0" {
  run "$VALIDATE_MIGRATIONS" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"migration"* ]]
}

@test "validate-migrations -h exits 0" {
  run "$VALIDATE_MIGRATIONS" -h
  [ "$status" -eq 0 ]
}

# ── No migrations ───────────────────────────────────────────────────────────

@test "no migrations exits 0" {
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"no migration files"* ]]
}

# ── Valid migrations ─────────────────────────────────────────────────────────

@test "valid migration passes all checks" {
  _make_migration "mycomp" "20260417-remove-something.sh"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"* ]]
}

@test "multiple valid migrations all pass" {
  _make_migration "compA" "20260417-first-migration.sh"
  _make_migration "compB" "20260501-second-migration.sh"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"* ]]
}

# ── Filename format validation ───────────────────────────────────────────────

@test "bad filename format fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/remove-something.sh" <<'EOF'
#!/usr/bin/env bash
migration_remove_something() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"filename must match"* ]]
}

@test "uppercase in slug fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-Remove-Thing.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_Remove_Thing() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"filename must match"* ]]
}

# ── Shebang validation ──────────────────────────────────────────────────────

@test "missing shebang fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
# no shebang
migration_20260417_test() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"shebang"* ]]
}

# ── Function name validation ────────────────────────────────────────────────

@test "wrong function name fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
wrong_name() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"expected function"* ]]
}

# ── Duplicate detection ─────────────────────────────────────────────────────

@test "duplicate filename across components fails" {
  _make_migration "compA" "20260417-shared-name.sh"
  _make_migration "compB" "20260417-shared-name.sh"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"duplicate"* ]]
}

# ── Quiet mode ───────────────────────────────────────────────────────────────

@test "--quiet suppresses per-check output but shows summary" {
  _make_migration "comp" "20260417-test.sh"
  _run_validate --quiet
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"* ]]
  # Quiet mode should not show individual check marks
  [[ "$output" != *"filename format valid"* ]]
}

# ── Mixed valid and invalid ─────────────────────────────────────────────────

@test "mixed valid and invalid reports correct error count" {
  _make_migration "compA" "20260417-good.sh"
  # Bad one: wrong function name
  local dir="$FAKE_WORKBENCH/compB/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260501-bad.sh" <<'EOF'
#!/usr/bin/env bash
wrong_func() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"1 of"*"failed"* ]]
}
