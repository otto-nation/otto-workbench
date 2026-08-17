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
  FAKE_CONFIG="$TMPDIR/config"
  # Never the real ~/.config/workbench: run_all_migrations empties this path.
  FAKE_LEGACY="$TMPDIR/legacy"
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
LIB_SRC_DIR="$FAKE_ROOT/lib"
WORKBENCH_STATE_DIR="$FAKE_STATE"
WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
LEGACY_WORKBENCH_ROOT="$FAKE_LEGACY"
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

# Helper: source the framework and run all migrations.
# Under `set -e`, matching the real caller (bin/otto-workbench). The marker
# printed afterwards is what proves the run returned rather than taking its
# caller down with it (#731) — a migration file's own `set -e` reaches this
# subshell through the source, so the abort is not hypothetical here.
run_migrations_in_fake() {
  (
    set -e
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    run_all_migrations
    echo "SYNC CONTINUED"
  )
}

# Helper: source the framework and adopt the legacy root only
adopt_in_fake() {
  (
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    adopt_legacy_workbench_root
  )
}

# ─── Legacy root adoption (#624) ─────────────────────────────────────────────

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
  # #730 deletes <state>/logs/ on purpose. Adoption runs before any migration
  # reads its bookkeeping, so carrying logs/ across would reinstate a directory
  # the migration that removed it is already recorded as applied for, and that
  # migration will never run again to take it back out (#732).
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
  # used to use — logs/ belongs to no root since #730, so adoption skips it.
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
  # (review_common.restore_preserved) — concatenating would misreport both runs.
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

# ─── Failure isolation (#731) ────────────────────────────────────────────────

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
  # a non-zero return used to exit the whole sync (#731). validate-migrations
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

# ─── Duplicate filename detection ───────────────────────────────────────────

@test "validator detects duplicate filenames across components" {
  create_migration "comp1" "20250101-dupe.sh" "migration_20250101_dupe"
  create_migration "comp2" "20250101-dupe.sh" "migration_20250101_dupe"

  run env WORKBENCH_DIR="$FAKE_ROOT" bash "$VALIDATOR"
  [ "$status" -eq 1 ]
  [[ "$output" == *"duplicate migration filename"* ]]
}

# ─── Config unification (#626) ───────────────────────────────────────────────

unify_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    # The real constants and config.sh, not the stubs: the migration writes to
    # WORKBENCH_CONFIG_FILE and seeds it through wb_config_ensure_file, which is
    # where the schema modeline comes from. The real ui.sh sources both the same
    # way, and constants.sh builds the file path from the root exported above.
    . "$REPO_ROOT/lib/constants.sh"
    . "$REPO_ROOT/lib/config.sh"
    . "$REPO_ROOT/bin/migrations/20260814-unify-workbench-config.sh"
    migration_20260814_unify_workbench_config
  )
}

@test "unification is a no-op when no legacy file exists" {
  run unify_in_fake
  [ "$status" -eq 0 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "unification folds every legacy file into config.yml" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"
  echo "lite" > "$FAKE_CONFIG/reuse-default"
  printf 'issue_tracker:\n  provider: github\n  team: ENG\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]

  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.reuse.default' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ "$(yq -r '.review.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.review.issue_tracker.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]

  [ ! -f "$FAKE_CONFIG/reuse-level" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
  [ -f "$FAKE_CONFIG/reuse-default.migrated" ]
  [ -f "$FAKE_CONFIG/review.yml.migrated" ]
}

@test "unification folds a partial set of legacy files" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.reuse.default // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "unification leaves a key config.yml already holds" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
}

@test "unification still folds keys an existing config.yml lacks" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "full" > "$FAKE_CONFIG/reuse-default"
  printf 'issue_tracker:\n  provider: jira\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ "$(yq -r '.reuse.default' "$FAKE_CONFIG/config.yml")" = "full" ]
  [ "$(yq -r '.review.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "unification renames a review.yml with nothing to carry" {
  mkdir -p "$FAKE_CONFIG"
  printf 'unrelated: true\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ -f "$FAKE_CONFIG/review.yml.migrated" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "unification seeds a new config.yml with the schema modeline" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]

  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
}

@test "unification leaves a config.yml the user already wrote unseeded" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  default: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" != *"yaml-language-server"* ]]
}

@test "unification re-run after a fold is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
}

@test "a mid-fold yq failure surfaces non-zero and leaves the source un-renamed" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"
  # Malformed YAML makes the fold's `yq` read fail rather than parse.
  printf 'issue_tracker: [unclosed\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not fold every setting into config.yml"* ]]

  # The failing fold's source is left in place, not renamed.
  [ -f "$FAKE_CONFIG/review.yml" ]
  [ ! -f "$FAKE_CONFIG/review.yml.migrated" ]
  # A fold that succeeded before the failure still carried its value over.
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
}
