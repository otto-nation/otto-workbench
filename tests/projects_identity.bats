#!/usr/bin/env bats
# Tests for the project registry's repo identity — memory orphans, backfill, and identity resolution.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  load 'projects_helper'
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and half these assertions compare the two.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # Everything a test builds lives in a temp directory, which is precisely what
  # the default exclusion list refuses. The sandboxed state root still keeps the
  # writes out of the real registry.
  # shellcheck disable=SC2034  # read by lib/projects.sh
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  common_teardown
}

# ─── Memory orphans ─────────────────────────────────────────────────────────

@test "memory_orphans reports nothing when \$WORKBENCH_MEMORY_DIR does not exist" {
  # WORKBENCH_MEMORY_DIR is derived from WORKBENCH_DATA_DIR once, at lib/ui.sh's
  # source time — setting the latter afterwards would not move it.
  WORKBENCH_MEMORY_DIR="$TMPDIR/data/memory"

  run memory_orphans
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "memory_orphans reports nothing when \$WORKBENCH_MEMORY_DIR is empty" {
  WORKBENCH_MEMORY_DIR="$TMPDIR/data/memory"
  mkdir -p "$WORKBENCH_MEMORY_DIR"

  run memory_orphans
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "memory_orphans skips a key whose repo is still registered" {
  # shellcheck source=../lib/ai/session-count.sh
  . "$REPO_ROOT/lib/ai/session-count.sh"
  WORKBENCH_MEMORY_DIR="$TMPDIR/data/memory"
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  record_project_repo_ids

  local key
  key="$(_repo_key "$TMPDIR/alpha")"
  mkdir -p "$WORKBENCH_MEMORY_DIR/$key"
  echo note > "$WORKBENCH_MEMORY_DIR/$key/topic.md"

  run memory_orphans
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "memory_orphans reports a key with no registered repo, and its file count" {
  WORKBENCH_MEMORY_DIR="$TMPDIR/data/memory"
  mkdir -p "$WORKBENCH_MEMORY_DIR/gone-key"
  echo note1 > "$WORKBENCH_MEMORY_DIR/gone-key/one.md"
  echo note2 > "$WORKBENCH_MEMORY_DIR/gone-key/two.md"
  # Not a .md file — not counted.
  echo raw > "$WORKBENCH_MEMORY_DIR/gone-key/notes.txt"

  run memory_orphans
  [ "$status" -eq 0 ]
  [ "$output" = "gone-key"$'\t'"2" ]
}

@test "memory_orphans reports one orphan and skips one live key together" {
  # shellcheck source=../lib/ai/session-count.sh
  . "$REPO_ROOT/lib/ai/session-count.sh"
  WORKBENCH_MEMORY_DIR="$TMPDIR/data/memory"
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  record_project_repo_ids

  local key
  key="$(_repo_key "$TMPDIR/alpha")"
  mkdir -p "$WORKBENCH_MEMORY_DIR/$key"
  echo note > "$WORKBENCH_MEMORY_DIR/$key/topic.md"
  mkdir -p "$WORKBENCH_MEMORY_DIR/gone-key"
  echo note > "$WORKBENCH_MEMORY_DIR/gone-key/topic.md"

  run memory_orphans
  [ "$status" -eq 0 ]
  [ "$output" = "gone-key"$'\t'"1" ]
}

# ─── Backfill ────────────────────────────────────────────────────────────────

@test "the backfill seeds the repos Claude Code recorded sessions in" {
  make_repo "$TMPDIR/alpha"
  mkdir -p "$TMPDIR/alpha/nested"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{},"%s":{}}}\n' "$TMPDIR/alpha" "$TMPDIR/alpha/nested" \
    > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]

  # Both entries resolve to the same work-tree root, so one line, not two.
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "the backfill reaches a bare-repo container's default worktree" {
  # `wt-init` and `worktrunk` put a bare repo at <container>/.git, and the
  # container is what Claude records when a session starts there. `rev-parse
  # --show-toplevel` refuses to run in it, so resolving by that alone dropped the
  # repo entirely — on a machine laid out this way, most of them.
  #
  # One row, not one per worktree: the branch the container's HEAD names stands
  # for the repo, and the feature worktrees come and go.
  make_bare_worktree_layout "$TMPDIR/container"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{}}}\n' "$TMPDIR/container" > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/container/main" ]
}

@test "the backfill runs once, even after the file already exists" {
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{}}}\n' "$TMPDIR/alpha" > "$CLAUDE_CONFIG_FILE"

  seed_project_registry
  project_forget "$TMPDIR/alpha"
  seed_project_registry

  run project_registered
  [ -z "$output" ]
}

@test "the backfill records itself even with nothing to seed" {
  CLAUDE_CONFIG_FILE="$TMPDIR/absent.json"
  run seed_project_registry
  [ "$status" -eq 0 ]
  run grep -c 'backfilled from' "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "the backfill does not retire itself when jq is missing" {
  # No jq means no candidates, which reads exactly like a machine that has none
  # — and recording the marker on that reading would retire the backfill before
  # it ever ran, which is the silent once-and-never-again failure it exists to
  # end.
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{}}}\n' "$TMPDIR/alpha" > "$CLAUDE_CONFIG_FILE"

  # Every directory that has one, not just the first: macOS ships a jq in
  # /usr/bin alongside whatever Homebrew put in front of it.
  local saved_path="$PATH" dir
  local -a keep=()
  while IFS= read -r dir; do
    if [[ -n "$dir" && ! -x "$dir/jq" ]]; then
      keep+=("$dir")
    fi
  done < <(printf '%s\n' "$PATH" | tr ':' '\n')
  PATH="$(IFS=:; printf '%s' "${keep[*]}")"

  run seed_project_registry
  PATH="$saved_path"

  [ "$status" -eq 0 ]
  run grep -c 'backfilled from' "$PROJECTS_REGISTRY_FILE"
  [ "$status" -ne 0 ]

  # jq arrives with the next sync, and the backfill is still there to run.
  seed_project_registry
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "the backfill does not retire itself when ~/.claude.json fails to parse" {
  # A file that exists but fails to parse (mid-write, hand-edited syntax
  # error) must not read like "no candidates" — that would record the marker
  # and retire the backfill on a machine that never had a successful read.
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":' > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]
  run grep -c 'backfilled from' "$PROJECTS_REGISTRY_FILE"
  [ "$status" -ne 0 ]

  # Once the file is fixed, the backfill still runs.
  printf '{"projects":{"%s":{}}}\n' "$TMPDIR/alpha" > "$CLAUDE_CONFIG_FILE"
  seed_project_registry
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a session cwd that is no longer a repo is skipped, not fatal" {
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{},"%s":{}}}\n' "$TMPDIR/gone" "$TMPDIR/alpha" \
    > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

# ─── Repo identity ───────────────────────────────────────────────────────────
#
# Every worktree of one repo names the same shared git dir, which is what lets
# work that belongs to the repo be done once instead of once per checkout.

@test "every worktree of one repo answers the same repo id" {
  make_bare_worktree_layout "$TMPDIR/container"

  run git_shared_dir "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/container/.git" ]

  run git_shared_dir "$TMPDIR/container/feature"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/container/.git" ]
}

@test "an ordinary clone's repo id is its own .git" {
  make_repo "$TMPDIR/alpha"

  run git_shared_dir "$TMPDIR/alpha"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha/.git" ]
}

@test "git_shared_dir refuses a directory that is not in a repo" {
  mkdir -p "$TMPDIR/plain"

  run git_shared_dir "$TMPDIR/plain"
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "a directory git cannot answer for stands for itself" {
  # Not an error: a repo-scoped migration visits it exactly once, which is what
  # per-checkout would have done anyway. Nothing is recorded, so the next sync
  # asks git again.
  mkdir -p "$TMPDIR/plain"

  run project_repo_id "$TMPDIR/plain"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/plain" ]
}

@test "record_project_repo_ids fills in the id of a registered worktree" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n' "$TMPDIR/container/main" > "$PROJECTS_REGISTRY_FILE"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/container/main"$'\t'"$TMPDIR/container/.git" ]
}

@test "record_project_repo_ids leaves a line it already resolved alone" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n' "$TMPDIR/container/main" > "$PROJECTS_REGISTRY_FILE"
  record_project_repo_ids
  local before
  before="$(cat "$PROJECTS_REGISTRY_FILE")"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  [ "$(cat "$PROJECTS_REGISTRY_FILE")" = "$before" ]
}

@test "record_project_repo_ids keeps the comment the backfill left" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '# backfilled from /somewhere\n%s\n' "$TMPDIR/alpha" \
    > "$PROJECTS_REGISTRY_FILE"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "${lines[0]}" = "# backfilled from /somewhere" ]
  [ "${lines[1]}" = "$TMPDIR/alpha"$'\t'"$TMPDIR/alpha/.git" ]
}

@test "record_project_repo_ids re-resolves an id whose directory is gone" {
  # A relayout — `git worktree move`, a container rename, a clone gone bare —
  # moves the shared git dir. Catching that costs a stat rather than the fork
  # re-resolving every line on every sync would.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/vanished/.git" \
    > "$PROJECTS_REGISTRY_FILE"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/alpha"$'\t'"$TMPDIR/alpha/.git" ]
}

@test "record_project_repo_ids records nothing for a directory git cannot answer for" {
  mkdir -p "$WORKBENCH_STATE_DIR" "$TMPDIR/plain"
  printf '%s\n' "$TMPDIR/plain" > "$PROJECTS_REGISTRY_FILE"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/plain" ]
}

@test "record_project_repo_ids clears a stale id it cannot re-resolve" {
  # The id's directory is gone, so it has to be re-resolved — but the path left
  # behind is one git cannot answer for either (no re-init here, so it is not a
  # work tree). The stale id must not survive untouched: project_repo_leaders
  # trusts any non-empty id field with no directory check of its own, so a
  # leftover id would keep standing in for a repo that no longer resolves to it.
  mkdir -p "$WORKBENCH_STATE_DIR" "$TMPDIR/plain"
  printf '%s\t%s\n' "$TMPDIR/plain" "$TMPDIR/vanished/.git" \
    > "$PROJECTS_REGISTRY_FILE"

  run record_project_repo_ids
  [ "$status" -eq 0 ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/plain" ]
}

@test "project_repo_worktrees puts a repo's worktrees together" {
  # Registration order is arrival order, so one repo's checkouts are scattered
  # through the file. Anything rendering the registry wants them consecutive.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/alpha" \
    "$TMPDIR/container/feature" > "$PROJECTS_REGISTRY_FILE"

  run project_repo_worktrees
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 3 ]
  [ "${lines[0]}" = "$TMPDIR/container/.git"$'\t'"$TMPDIR/container/main" ]
  [ "${lines[1]}" = "$TMPDIR/container/.git"$'\t'"$TMPDIR/container/feature" ]
  [ "${lines[2]}" = "$TMPDIR/alpha/.git"$'\t'"$TMPDIR/alpha" ]
}

@test "project_repo_worktrees skips a worktree that is gone" {
  # The same read-time drop every other consumer gets, so a removed checkout
  # stops being rendered before the sync's prune gets to the line.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/container/feature" \
    > "$PROJECTS_REGISTRY_FILE"
  rm -rf "$TMPDIR/container/feature"

  run project_repo_worktrees
  [ "$output" = "$TMPDIR/container/.git"$'\t'"$TMPDIR/container/main" ]
}

@test "a repo id names the directory its repository lives at" {
  run project_repo_label "$TMPDIR/container/.git"
  [ "$output" = "$TMPDIR/container" ]
}

@test "a repo id that is not a .git stands for itself" {
  # A bare clone kept as <name>.git, and the work tree project_repo_id falls
  # back to when git could name no shared dir — neither has a parent to climb to.
  run project_repo_label "$TMPDIR/mirror.git"
  [ "$output" = "$TMPDIR/mirror.git" ]

  run project_repo_label "$TMPDIR/plain"
  [ "$output" = "$TMPDIR/plain" ]
}

@test "project_repo_leaders names one worktree per repo" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/container/feature" \
    "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run project_repo_leaders
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 2 ]
  [ "${lines[0]}" = "$TMPDIR/container/.git"$'\t'"$TMPDIR/container/main" ]
  [ "${lines[1]}" = "$TMPDIR/alpha/.git"$'\t'"$TMPDIR/alpha" ]
}

@test "project_repo_leaders picks the next worktree when the leader is gone" {
  # The leader is whichever registered worktree of the repo is still there and
  # comes first. It does not have to be stable — a repo-scoped state line names
  # the repo, so a different leader re-runs nothing.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/container/feature" \
    > "$PROJECTS_REGISTRY_FILE"
  rm -rf "$TMPDIR/container/main"

  run project_repo_leaders
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/container/.git"$'\t'"$TMPDIR/container/feature" ]
}

@test "project_repo_leaders reads the id the registry already holds" {
  # No fork per line: the sync resolved these once, and the pruning that runs
  # before every migration reads them back.
  mkdir -p "$WORKBENCH_STATE_DIR" "$TMPDIR/checkout"
  printf '%s\t%s\n' "$TMPDIR/checkout" "$TMPDIR/elsewhere/.git" \
    > "$PROJECTS_REGISTRY_FILE"

  run project_repo_leaders
  [ "$output" = "$TMPDIR/elsewhere/.git"$'\t'"$TMPDIR/checkout" ]
}
