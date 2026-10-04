#!/usr/bin/env bats
# Tests for lib/migrations.sh — absent targets, repo-scoped migrations, the registry prune, discovery under set -e.
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

@test "a checkout-scoped migration defers per repo, not for the machine" {
  # One repo has the target and one does not, so the run has to record the first
  # and leave the second to be asked again.
  mkdir -p "$FAKE_ROOT/mycomp/migrations"
  cat > "$FAKE_ROOT/mycomp/migrations/20250101-proj.sh" <<EOF
#!/usr/bin/env bash
set -e
# checkout-scoped: edits files inside each repo.
migration_20250101_proj() {
  [[ -f "\$1/target" ]] || return "\$MIGRATION_DEFERRED"
}
EOF
  register_fake_project "$TMPDIR/repo-ready"
  register_fake_project "$TMPDIR/repo-later"
  touch "$TMPDIR/repo-ready/target"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  assert_checkout_entry mycomp/20250101-proj.sh "$TMPDIR/repo-ready"
  run ! grep -qF "repo-later" "$FAKE_STATE/migrations.applied"
}

# ─── Repo-scoped migrations ──────────────────────────────────────────────────
#
# Work that belongs to a repository rather than to one of its checkouts is done
# once and recorded once. Every worktree of one bare-repo container shares a git
# dir, and that is the name the state line carries — so removing the worktree
# the migration happened to run in leaves the entry standing.

# Helper: create a migration that declares itself repo-scoped and appends the
# path it was handed to $TMPDIR/exec.log.
create_repo_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
# repo-scoped: edits files the whole repo shares.
${fn_name}() {
  echo "\$1" >> "$TMPDIR/exec.log"
}
EOF
}

# Helper: a bare-repo container with two worktrees, both registered, and
# FAKE_CONTAINER set to its resolved path.
#
# Composed from the shared fixtures in test_helper.bash — make_container_seed
# for the seed repo (branches main and feat), make_worktree_container for the
# bare clone with `main` checked out — plus one more `git worktree add` for a
# second checkout on the branch the seed already created.
#
# Resolved with `pwd -P` because git reports the /private twin of a macOS
# /var/folders temp path, and the registry entries have to be the paths git
# will answer with.
register_fake_repo_worktrees() {
  local name="$1" seed
  FAKE_CONTAINER="$(cd "$TMPDIR" && pwd -P)/$name"
  seed="$FAKE_CONTAINER.seed"
  mkdir -p "$seed"
  printf 'x\n' > "$seed/a.txt"
  make_container_seed "$seed"
  make_worktree_container "$FAKE_CONTAINER" "$seed"
  git -C "$FAKE_CONTAINER" worktree add -q "$FAKE_CONTAINER/feature" feat
  rm -rf "$seed"
  printf '%s\n%s\n' "$FAKE_CONTAINER/main" "$FAKE_CONTAINER/feature" \
    >> "$FAKE_STATE/projects.registry"
}

@test "a repo-scoped migration runs once for a repo with several worktrees" {
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees container

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-repo.sh (1 project)"* ]]

  [ "$(cat "$TMPDIR/exec.log")" = "$FAKE_CONTAINER/main" ]
  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-repo.sh"$'\t'"$FAKE_CONTAINER/.git" ]
}

@test "a repo-scoped entry survives the worktree it ran in being removed" {
  # The whole point: `wt remove` used to orphan an entry per worktree per
  # migration, and the next sync forgot them by the dozen.
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees container
  run_migrations_in_fake

  rm -rf "$FAKE_CONTAINER/main"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Migration applied"* ]]
  [ "$(wc -l < "$TMPDIR/exec.log")" -eq 1 ]
  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-repo.sh"$'\t'"$FAKE_CONTAINER/.git" ]
}

@test "a repo-scoped migration visits a repo registered later" {
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees alpha
  local alpha="$FAKE_CONTAINER"
  run_migrations_in_fake

  register_fake_repo_worktrees beta

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Migration applied: 20250101-repo.sh (1 project)"* ]]

  run cat "$TMPDIR/exec.log"
  [ "${#lines[@]}" -eq 2 ]
  [ "${lines[0]}" = "$alpha/main" ]
  [ "${lines[1]}" = "$FAKE_CONTAINER/main" ]
}

@test "a repo-scoped entry is dropped when the last checkout of its repo goes" {
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees container
  run_migrations_in_fake

  rm -rf "$FAKE_CONTAINER"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [ ! -s "$FAKE_STATE/migrations.applied" ]
}

@test "per-checkout entries are dropped when a migration becomes repo-scoped" {
  # How a migration converts scope, in the direction this change makes: the
  # per-checkout lines name no repo the new shape would ever write, so prune
  # drops them and the repo is visited once. No re-dating the file.
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees container
  printf 'mycomp/20250101-repo.sh\t%s\nmycomp/20250101-repo.sh\t%s\n' \
    "$FAKE_CONTAINER/main" "$FAKE_CONTAINER/feature" \
    > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  # Not stale — the file is still there, it just records itself differently now.
  [[ "$output" != *"Pruned stale migration state"* ]]

  [ "$(cat "$TMPDIR/exec.log")" = "$FAKE_CONTAINER/main" ]
  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-repo.sh"$'\t'"$FAKE_CONTAINER/.git" ]
}

@test "a repo-scoped entry is dropped when the migration becomes checkout-scoped" {
  # The other direction: a line naming a shared git dir means nothing to a
  # migration recorded per checkout, and no line the framework writes for it
  # would ever match.
  create_checkout_migration mycomp 20250101-proj.sh migration_20250101_proj
  register_fake_repo_worktrees container
  printf 'mycomp/20250101-proj.sh\t%s\n' "$FAKE_CONTAINER/.git" \
    > "$FAKE_STATE/migrations.applied"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run cat "$FAKE_STATE/migrations.applied"
  [ "${lines[0]}" = "mycomp/20250101-proj.sh"$'\t'"$FAKE_CONTAINER/main" ]
  [ "${lines[1]}" = "mycomp/20250101-proj.sh"$'\t'"$FAKE_CONTAINER/feature" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "a repo git cannot answer for is visited once and recorded by its path" {
  # A registered directory that is no longer a repository still gets visited
  # exactly once, which is what per-checkout would have done for it anyway.
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_project "$TMPDIR/plain"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/exec.log")" = "$TMPDIR/plain" ]
  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-repo.sh"$'\t'"$TMPDIR/plain" ]
}

@test "this repo's repo-scoped migrations are discovered by their real keys" {
  # Against the real workbench rather than the fake one: the marker is a
  # contract with files that ship, and a typo in one is invisible otherwise.
  run bash -c "
    WORKBENCH_DIR='$REPO_ROOT'
    LIB_SRC_DIR='$REPO_ROOT/lib'
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    keys=()
    _discover_migration_keys keys \"\$_REPO_SCOPED_MARKER\"
    printf '%s\n' \"\${keys[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"ai/claude/20260824-drop-container-anatomy.sh"* ]]
}

# ─── The sync's registry prune ───────────────────────────────────────────────
#
# Every read already skips a registered path that has gone, so nothing here is
# about what the sweeps above can see. It is about what the file keeps: a
# machine that cuts and removes worktrees routinely accumulates dead lines
# faster than anything adds a repo, and each one is stat-ed again on every sync
# for as long as it is stored.

@test "the sync drops a registry entry whose work tree has gone" {
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  rm -rf "$TMPDIR/repo-a"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run grep -v '^#' "$FAKE_STATE/projects.registry"
  [ "$output" = "$TMPDIR/repo-b" ]
}

@test "the sync says what it pruned, and only when it pruned something" {
  # A settled machine drops nothing on every sync thereafter, and a line saying
  # so each time is noise the operator cannot act on.
  register_fake_project "$TMPDIR/repo-a"
  register_fake_project "$TMPDIR/repo-b"
  rm -rf "$TMPDIR/repo-a"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" == *"Project registry pruned — 1 entry(s) whose work tree is gone"* ]]

  run run_migrations_in_fake
  [ "$status" -eq 0 ]
  [[ "$output" != *"Project registry pruned"* ]]
}

@test "the sync's prune keeps the comment that retires the backfill" {
  # The marker seed_project_registry leaves behind is a comment line, and the
  # prune runs straight after it. Dropping the comment would put the machine
  # back to backfilling on every sync.
  printf '# backfilled from %s\n' "$TMPDIR/absent-claude.json" \
    > "$FAKE_STATE/projects.registry"
  register_fake_project "$TMPDIR/repo-a"
  rm -rf "$TMPDIR/repo-a"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run cat "$FAKE_STATE/projects.registry"
  [ "$output" = "# backfilled from $TMPDIR/absent-claude.json" ]
}

@test "a removed worktree leaves the registry and its repo's entry stays" {
  # The routine case `wt remove` produces: one checkout of a repo that has
  # others. The line goes, and the repo-scoped state entry does not — it is
  # keyed on the shared git dir, which a surviving sibling still leads.
  create_repo_migration mycomp 20250101-repo.sh migration_20250101_repo
  register_fake_repo_worktrees container
  run_migrations_in_fake

  rm -rf "$FAKE_CONTAINER/main"

  run run_migrations_in_fake
  [ "$status" -eq 0 ]

  run grep -v '^#' "$FAKE_STATE/projects.registry"
  [ "$output" = "$FAKE_CONTAINER/feature"$'\t'"$FAKE_CONTAINER/.git" ]

  run cat "$FAKE_STATE/migrations.applied"
  [ "$output" = "mycomp/20250101-repo.sh"$'\t'"$FAKE_CONTAINER/.git" ]
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
