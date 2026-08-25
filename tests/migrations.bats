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
PROJECTS_REGISTRY_FILE="$FAKE_STATE/projects.registry"
# No such file, so the backfill run_all_migrations does has no candidates.
CLAUDE_CONFIG_FILE="$TMPDIR/absent-claude.json"
# The real projects.sh, not a stub: run_all_migrations calls into it, and it
# needs the constants above, so it loads from here rather than from ui.sh.
. "$FAKE_ROOT/lib/projects.sh"
CONST

  # Source the real component discovery and migrations libraries with our fake paths
  cp "$REPO_ROOT/lib/components.sh" "$FAKE_ROOT/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$FAKE_ROOT/lib/migrations.sh"
  cp "$REPO_ROOT/lib/projects.sh" "$FAKE_ROOT/lib/projects.sh"
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
# caller down with it — a migration file's own `set -e` reaches this
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

# ─── Project-scoped migrations ───────────────────────────────────────────────
#
# A migration that edits files inside a repo is done per repo, not per machine.
# The marker in its header hands the loop over the registry to the framework,
# which records one state line per repo it visited — so a repo the machine
# learns about later is a key the state file simply does not hold yet.

# Helper: create a migration that declares itself project-scoped and appends the
# repo path it was handed to $TMPDIR/exec.log.
create_project_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
# project-scoped: edits files inside each repo.
${fn_name}() {
  echo "\$1" >> "$TMPDIR/exec.log"
}
EOF
}

# Helper: put a repo in the registry.
#
# Written straight into the file rather than through project_register, which
# refuses anything under \$TMPDIR — and a temp directory is the only place a
# test may build a repo. project_registered, which is what the framework reads,
# applies no such rule.
register_fake_project() {
  mkdir -p "$1"
  printf '%s\n' "$1" >> "$FAKE_STATE/projects.registry"
}

# Helper: assert the state file holds KEY's entry for a repo. The separator the
# framework writes lives here rather than in every assertion that reads one back.
assert_project_entry() {
  local key="$1" repo="$2"
  grep -qxF "$key"$'\t'"$repo" "$FAKE_STATE/migrations.applied"
}

@test "a project-scoped migration runs once per registered repo" {
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (2 projects)"* ]]

  run cat "$TMPDIR/exec.log"
  [ "${lines[0]}" = "$TMPDIR/repo-a" ]
  [ "${lines[1]}" = "$TMPDIR/repo-b" ]

  # One line per repo, and no bare key: a bare key is the machine claiming to be
  # done, which is the whole thing a project-scoped migration cannot say.
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
  run ! grep -qxF "mycomp/20250101-proj.sh" "$FAKE_STATE/migrations.applied"
}

@test "a project-scoped migration on a machine with no repos records nothing" {
  # No repo to visit is not the migration being done — it is a machine that has
  # nothing to apply it to yet. Recording anything here is what would leave the
  # first repo registered afterwards unvisited, so the run is silent and the
  # state file stays empty until there is a repo to name.
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
  [ ! -e "$TMPDIR/exec.log" ]

  register_fake_project "$TMPDIR/repo-a"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-proj.sh (1 project)"* ]]
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
}

@test "a repo that registers after the first sync is visited on the next" {
  # The bug this whole shape exists for: under a single machine-wide key the
  # first sync recorded the migration as done and repo-b never received it.
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
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
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  run_migrations_in_fake

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pruned stale migration state"* ]]

  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 1 ]
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
}

@test "entries naming a repo that left the registry are dropped" {
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  run_migrations_in_fake

  rm -rf "$TMPDIR/repo-b"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"no longer registered"* ]]

  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  run ! grep -qF "repo-b" "$FAKE_STATE/migrations.applied"
}

@test "a repo path holding a tab keeps the key ahead of it intact" {
  # Every split is on the first separator, so a path carrying one costs the repo
  # nothing: the key still matches a discovered migration and the entry is not
  # pruned as unrecognised.
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/re"$'\t'"po"
  run_migrations_in_fake

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pruned stale migration state"* ]]
  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 1 ]
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/re"$'\t'"po"
}

@test "a bare entry for a migration that became project-scoped is dropped" {
  # How a migration converts scope: the machine-wide line the old shape recorded
  # says nothing about any repo, so prune drops it and every registered repo is
  # visited. Re-dating the file to force that is not needed.
  create_project_migration mycomp 20250101-proj.sh migration_20250101_proj
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

# Helper: a project-scoped migration that fails for any repo holding a `fail`
# file, and records the rest.
create_failing_project_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
# project-scoped: edits files inside each repo.
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
  create_failing_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  touch "$TMPDIR/repo-b/fail"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration failed: 20250101-proj.sh in $TMPDIR/repo-b"* ]]
  [[ "$output" == *"SYNC CONTINUED"* ]]

  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
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
# project-scoped: drains it inside each repo.
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

@test "this repo's project-scoped migrations are discovered by their real keys" {
  # Against the real tree, for the reason the adoption-sensitive twin above is:
  # a typo in the marker line is invisible everywhere else, and the migration
  # would quietly go back to claiming the whole machine after one run.
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    keys=()
    _discover_migration_keys keys \"\$_PROJECT_SCOPED_MARKER\"
    printf '%s\n' \"\${keys[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ai/claude/20260819-context-to-architecture.sh"* ]]
}

# ─── Migrations that find nothing to do ──────────────────────────────────────
#
# A migration has to be idempotent, so "already in the target shape" is its
# commonest outcome and, for a project-scoped one on a machine that registers a
# worktree whenever one is opened, very nearly its only outcome. Returning 0
# there reported a rename that could never happen as work applied, once per
# sync, forever. MIGRATION_NOOP is how a migration says which of the two it did.

# Helper: a project-scoped migration that answers MIGRATION_NOOP for any repo
# not holding a `work` file, and logs every repo it is handed either way.
create_noop_project_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
set -e
# project-scoped: edits files inside each repo.
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
  create_noop_project_migration mycomp 20250101-proj.sh migration_20250101_proj
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
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-c"
}

@test "a sync whose only new repo has nothing to do says nothing" {
  # The shape that put a line in every sync: a worktree registers itself the
  # first time anything runs in it, the framework visits it because the state
  # file does not name it yet, and the visit finds the work already done.
  create_noop_project_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_project "$TMPDIR/repo-a"
  touch "$TMPDIR/repo-a/work"
  run_migrations_in_fake

  register_fake_project "$TMPDIR/repo-b"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-b"
}

@test "a repo that fails is still a failure alongside one that no-ops" {
  # MIGRATION_NOOP is one specific status, not "any non-zero is fine now".
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# project-scoped: edits files inside each repo.
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

  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-a"
  run ! grep -qF "repo-b" "$FAKE_STATE/migrations.applied"
}

@test "changed, unchanged and failed repos are each reported as themselves" {
  # All three outcomes in one run, because the count, the warning and the state
  # file each read a different one and only a mixed run can tell them apart.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# project-scoped: edits files inside each repo.
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
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-work"
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-noop"
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

# ─── Migrations whose target does not exist yet ──────────────────────────────
#
# The absence a no-op reports is final: the target has been looked at and holds
# the shape the migration produces. The absence here is not — the target has not
# been created yet, and something later in the same sync, or a session an hour
# afterwards, may create it. Recording that as done is what retired
# 20260819-lift-issue-tracker-key against a config.yml it never saw.

@test "a migration whose target does not exist yet is not recorded" {
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-defer.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250101_defer() {
  echo "CALLED" >> "$TMPDIR/exec.log"
  [[ -f "$TMPDIR/target" ]] || return "\$MIGRATION_DEFERRED"
}
EOF

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"SYNC CONTINUED"* ]]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
}

@test "a deferred migration says nothing" {
  # Silence is the price of the retry: this can be answered on every sync for as
  # long as the target stays absent, so anything printed here prints forever.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-defer.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250101_defer() {
  return "\$MIGRATION_DEFERRED"
}
EOF

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  [[ "$output" != *"Migration failed"* ]]
  [[ "$output" != *"migrations:"* ]]
}

@test "a deferred migration runs again once its target exists" {
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-defer.sh" <<EOF
#!/usr/bin/env bash
set -e
migration_20250101_defer() {
  echo "CALLED" >> "$TMPDIR/exec.log"
  [[ -f "$TMPDIR/target" ]] || return "\$MIGRATION_DEFERRED"
  rm -f "$TMPDIR/target"
}
EOF

  run_migrations_in_fake
  touch "$TMPDIR/target"
  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-defer.sh"* ]]
  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 2 ]
  [ ! -e "$TMPDIR/target" ]
  grep -qxF "mycomp/20250101-defer.sh" "$FAKE_STATE/migrations.applied"
}

@test "a project-scoped migration defers per repo, not for the machine" {
  # One repo has the target and one does not, so the run has to record the first
  # and leave the second to be asked again.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# project-scoped: edits files inside each repo.
migration_20250101_proj() {
  [[ -f "\$1/target" ]] || return "\$MIGRATION_DEFERRED"
}
EOF
  register_fake_project "$TMPDIR/repo-ready"
  register_fake_project "$TMPDIR/repo-later"
  touch "$TMPDIR/repo-ready/target"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  assert_project_entry mycomp/20250101-proj.sh "$TMPDIR/repo-ready"
  run ! grep -qF "repo-later" "$FAKE_STATE/migrations.applied"
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

# ─── Config unification ──────────────────────────────────────────────────────

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
    # For MIGRATION_NOOP and MIGRATION_DEFERRED, which the migration body
    # returns by name. The subshell keeps them from reaching the assertions
    # outside, which spell the numbers out.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260814-unify-workbench-config.sh"
    migration_20260814_unify_workbench_config
  )
}

@test "unification is a no-op when no legacy file exists" {
  # NOOP, not deferred: nothing writes the three legacy files any more, so a
  # machine without them will not grow them, and the adoption that can re-seed
  # them forgets this migration's state line outright.
  run unify_in_fake
  [ "$status" -eq 3 ]
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
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issue_tracker.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]

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
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "unification renames a review.yml with nothing to carry" {
  mkdir -p "$FAKE_CONFIG"
  printf 'unrelated: true\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ -f "$FAKE_CONFIG/review.yml.migrated" ]
  [ "$(yq -r '.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
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
  [ "$status" -eq 3 ]
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

# ─── Issue tracker key lift ──────────────────────────────────────────────────

lift_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in unify_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260824-lift-issue-tracker-key.sh"
    migration_20260824_lift_issue_tracker_key
  )
}

@test "lift defers while there is no config.yml" {
  # The bug this migration was re-dated for: the 20260819 version returned 0
  # here, was recorded, and had nothing left to lift when a session wrote the
  # legacy shape into a new config.yml half an hour later.
  run lift_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "lift is a no-op when the key is already top-level" {
  # Recorded rather than deferred: the file is here and holds no legacy key, and
  # nothing writes .review.issue_tracker any more.
  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "lift picks up a config.yml written after an earlier sync deferred it" {
  # A deferred migration is not recorded, so the framework asks again — and the
  # legacy shape a later session wrote is lifted on that pass.
  run lift_in_fake
  [ "$status" -eq 4 ]

  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "lift moves the whole legacy mapping to the top level" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n    team: ENG\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issue_tracker.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]
  [ "$(yq -r '.review.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift drops a review section it emptied" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: jira\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift keeps a review section holding other settings" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: high\n  issue_tracker:\n    provider: jira\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "lift keeps a top-level value already written against the new schema" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: jira\nissue_tracker:\n  provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift preserves the schema modeline and hand-written comments" {
  mkdir -p "$FAKE_CONFIG"
  printf '# yaml-language-server: $schema=https://example/config.schema.json\n# we file on GitHub\nreview:\n  issue_tracker:\n    provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# we file on GitHub" "$FAKE_CONFIG/config.yml"
}

@test "lift re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  run lift_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

# ─── Agent config section ────────────────────────────────────────────────────

agent_section_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in lift_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260824-agent-config-section.sh"
    migration_20260824_agent_config_section
  )
}

@test "agent section defers while there is no config.yml" {
  # Deferred, not recorded: a session that writes the legacy shape into a new
  # config.yml after this sync still has the move waiting for it.
  run agent_section_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "agent section is a no-op when review holds none of the keys" {
  # Recorded rather than deferred: the file is here and holds no legacy key, and
  # nothing writes review.model any more.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: high\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
}

@test "agent section picks up a config.yml written after a deferred sync" {
  run agent_section_in_fake
  [ "$status" -eq 4 ]

  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
}

@test "agent section moves every sizing key across" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n  thinking: high\n  provider: pi\n  effort: high\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
  [ "$(yq -r '.agent.thinking' "$FAKE_CONFIG/config.yml")" = "high" ]
  [ "$(yq -r '.agent.provider' "$FAKE_CONFIG/config.yml")" = "pi" ]
  [ "$(yq -r '.review.model // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
  [ "$(yq -r '.review.thinking // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
  [ "$(yq -r '.review.provider // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section moves the phases mapping whole" {
  # phases is the one nested value: every per-phase entry has to survive the
  # move, not just the key naming them.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  phases:\n    scout:\n      model: haiku\n    group:\n      thinking: low\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.phases.scout.model' "$FAKE_CONFIG/config.yml")" = "haiku" ]
  [ "$(yq -r '.agent.phases.group.thinking' "$FAKE_CONFIG/config.yml")" = "low" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section keeps effort behind in review" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: low\n  model: sonnet\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "low" ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "sonnet" ]
}

@test "agent section drops a review section it emptied" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section keeps a value already written against the new schema" {
  # A machine that hand-wrote agent.model before the sync reached it: the value
  # under the key the loader now reads is the one the operator chose last.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\nagent:\n  model: sonnet\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "sonnet" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section preserves the schema modeline and hand-written comments" {
  mkdir -p "$FAKE_CONFIG"
  printf '# yaml-language-server: $schema=https://example/config.schema.json\n# opus reviews better\nreview:\n  model: opus\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# opus reviews better" "$FAKE_CONFIG/config.yml"
}

@test "agent section re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n  effort: high\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  run agent_section_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
}
