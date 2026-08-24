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

# ── File scope ──────────────────────────────────────────────────────────────

@test "self-invocation at file scope fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
set -e
migration_20260417_test() { echo "hi"; }
migration_20260417_test
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"file scope must only define migration_20260417_test()"* ]]
  [[ "$output" == *"20260417-test.sh:4"* ]]
}

@test "any executable statement at file scope fails, not just a self-call" {
  # The framework sources the file before it calls anything, so a statement out
  # here runs on that pass. Under the file's own `set -e` a failing one used to
  # take the sync with it; the shape is the problem, not the name being called.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
set -e
_precondition() { return 1; }
_precondition
migration_20260417_test() { echo "hi"; }
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"file scope must only define migration_20260417_test()"* ]]
  [[ "$output" == *"_precondition"* ]]
}

@test "a self-call from inside a helper body is not a file-scope statement" {
  # Function bodies are stripped by brace-counting, so the call below is inside
  # _dispatch however it is indented. A line-shape check reads it as file scope.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
set -e
_dispatch() {
case "$1" in
again)
migration_20260417_test
;;
esac
}
migration_20260417_test() { _dispatch skip; }
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "braces inside heredocs and quotes do not expose a function body" {
  # A stray brace in a heredoc or a string would unbalance a naive count and
  # make the rest of the body look like file scope.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
set -e
migration_20260417_test() {
  cat <<'TXT'
} not a closing brace {
TXT
  local stray='} nor this {'
  echo "$stray"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "self-invocation guarded with || true still fails" {
  # `|| true` keeps the sourcing pass from aborting, but the line still runs
  # the migration a second time and the run whose status the framework reads
  # is the other one — there is no version of this call that has a job to do.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() { echo "hi"; }
migration_20260417_test || true
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"file scope"* ]]
}

@test "a spaced function definition is not read as a self-invocation" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test () {
  echo "hi"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

# ── Scope marker ────────────────────────────────────────────────────────────
#
# The framework calls a project-scoped migration once per registered repo with
# that repo's path, and every other migration with no arguments at all. The two
# ways the header and the signature can disagree are both silent at runtime:
# a marked function that ignores the path does the same global thing once per
# repo, and an unmarked one that reads $1 works on an empty string.

@test "a project-scoped migration that reads the repo path passes" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
# project-scoped: edits files inside each repo.
migration_20260417_test() {
  local project_dir="$1"
  rm -f "$project_dir/.claude/stale"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a marked migration that ignores the repo path fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
# project-scoped: edits files inside each repo.
migration_20260417_test() {
  echo "hi"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"never reads the repo path"* ]]
}

@test "an unmarked migration that reads an argument fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  echo "$1"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"no '# project-scoped:' header"* ]]
}

@test "a positional read inside a nested helper is not the migration's own" {
  # ai/claude/20260624-workbench-state-dir.sh has this shape: the migration
  # defines a helper inside its own body and calls it per root. The $1 belongs
  # to the helper, and the framework still calls the migration with nothing.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  _per_root() {
    local root="$1"
    echo "$root"
  }
  _per_root /one
  _per_root /two
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a positional read in a helper beside the function is not the migration's own" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
_per_root() {
  echo "$1"
}
migration_20260417_test() {
  _per_root /one
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "the body resumes after a nested helper closes" {
  # The nested body is skipped for its text, not to the end of the migration —
  # a marked migration reading the path after its helper still passes.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
# project-scoped: edits files inside each repo.
migration_20260417_test() {
  _per_root() {
    local root="$1"
    echo "$root"
  }
  _per_root /one
  rm -f "$1/.claude/stale"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a one-line body ends the scan at its own closing brace" {
  # The brace pair opens and closes on the definition line, so the scan has to
  # end there rather than run on into whatever follows the function. The file
  # scope statement below is a violation of its own — the point here is that it
  # is not also reported as the migration reading an argument.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() { rm -f /some/file; }
echo "$1"
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"runs when the framework sources the file"* ]]
  [[ "$output" != *"reads a positional argument"* ]]
}

@test "the repo path read through an unquoted heredoc counts as a read" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
# project-scoped: edits files inside each repo.
migration_20260417_test() {
  cat <<MSG
Migrating $1
MSG
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a quoted heredoc body is literal text, not an argument read" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  cat <<'MSG'
Pass the repo as $1
MSG
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a single-quoted dollar-one is literal text, not an argument read" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  sed -i.bak 's/$1/literal/' /some/file
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

# ── Absent-target guards ────────────────────────────────────────────────────
#
# A migration meets two absences that look the same in the source: a target
# already in the shape it produces, and a target that does not exist yet. Only
# the returned status separates them, and `return 0` picks "retired for good"
# by accident — which is how the 20260819 lift was recorded against a config.yml
# that a session created half an hour later.

@test "a bare return 0 under an absent-file guard fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$SOME_FILE" ]] || return 0
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"records the migration as applied against a file it never saw"* ]]
}

@test "an absent-file guard returning MIGRATION_DEFERRED passes" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$SOME_FILE" ]] || return "$MIGRATION_DEFERRED"
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "an absent-file guard returning MIGRATION_NOOP passes" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$SOME_FILE" ]] || return "$MIGRATION_NOOP"
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a negated test returning 0 with && fails the same way" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ ! -d "$SOME_DIR" ]] && return 0
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"records the migration as applied against a file it never saw"* ]]
}

@test "a return 0 on the line after an if testing for absence fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  if [[ ! -f "$OLD_FILE" ]]; then
    return 0
  fi
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"records the migration as applied against a file it never saw"* ]]
}

@test "returning 0 because the target is present is not an absent-target guard" {
  # The mirror image of the guarded shape: this returns when the file is there,
  # which is the migration having already run, not a target that never arrived.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$NEW_FILE" ]] && return 0
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "a non-path test returning 0 is left alone" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  local value="x"
  [[ -n "$value" ]] || return 0
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "an absent-file guard returning 1 is a failure, not a silent record" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$SOME_FILE" ]] || return 1
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

@test "an absent-file guard inside a nested helper is not the migration's own" {
  # 20260814-unify-workbench-config.sh has this shape: its folders return to the
  # migration function, not to the framework, so their 0 is an ordinary return.
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
_fold() {
  [[ -f "$1" ]] || return 0
  echo "folding"
}
migration_20260417_test() {
  _fold /one
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

# ── Deferred reachability ───────────────────────────────────────────────────
#
# Nothing records a deferred migration, so the status has to stay pinned to a
# condition that resolves on its own. One returned on any other condition runs
# again on every sync for as long as the file exists.

@test "MIGRATION_DEFERRED outside an absent-target guard fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  local value
  value="$(cat /some/file)"
  [[ -n "$value" ]] || return "$MIGRATION_DEFERRED"
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"only for a target that does not exist yet"* ]]
}

@test "MIGRATION_DEFERRED returned when the target is present fails" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  [[ -f "$NEW_FILE" ]] && return "$MIGRATION_DEFERRED"
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"only for a target that does not exist yet"* ]]
}

@test "MIGRATION_DEFERRED under an if testing for absence passes" {
  local dir="$FAKE_WORKBENCH/comp/migrations"
  mkdir -p "$dir"
  cat > "$dir/20260417-test.sh" <<'EOF'
#!/usr/bin/env bash
migration_20260417_test() {
  if [[ ! -e "$SOME_PATH" ]]; then
    return "${MIGRATION_DEFERRED}"
  fi
  echo "converting"
}
EOF
  _run_validate
  [ "$status" -eq 0 ]
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
