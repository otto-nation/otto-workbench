#!/usr/bin/env bats
# Tests for the project registry — lib/projects.sh, its Python half
# (ai/lib/workbench_projects.py), the agreement between the two, and the
# `otto-workbench projects` CLI.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and half these assertions compare the two.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # Everything a test builds lives in a temp directory, which is precisely what
  # the default exclusion list refuses. The sandboxed state root still keeps the
  # writes out of the real registry.
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# make_repo DIR — a git work tree at DIR.
make_repo() {
  mkdir -p "$1"
  GIT_CEILING_DIRECTORIES="$(dirname "$1")" git -C "$1" init --quiet
}

# make_bare_container DIR — the layout wt-init produces: a bare repo at
# DIR/.git with per-branch worktrees beside it.
make_bare_container() {
  mkdir -p "$1"
  git init --bare --quiet "$1/.git"
}

# make_bare_worktree_layout DIR — a bare container with the branch its HEAD
# names checked out at DIR/main, and a feature worktree beside it.
make_bare_worktree_layout() {
  local container="$1" seed="$1.seed"
  make_repo "$seed"
  git -C "$seed" -c user.email=t@example.com -c user.name=t commit --allow-empty -qm init
  git -C "$seed" branch -qM main
  mkdir -p "$container"
  git clone --bare --quiet "$seed" "$container/.git"
  git --git-dir="$container/.git" worktree add "$container/main" main >/dev/null 2>&1
  git --git-dir="$container/.git" worktree add -b feature "$container/feature" >/dev/null 2>&1
  rm -rf "$seed"
}

# ─── Registration ────────────────────────────────────────────────────────────

@test "a registered repo comes back from project_registered" {
  make_repo "$TMPDIR/alpha"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "registering twice leaves one line" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 3 ]

  run project_registered
  [ "${#lines[@]}" -eq 1 ]
}

@test "a trailing slash is the same repo" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha/"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 3 ]

  run project_registered
  [ "${#lines[@]}" -eq 1 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a directory that is not a git work tree is refused" {
  mkdir -p "$TMPDIR/plain"
  run project_register "$TMPDIR/plain"
  [ "$status" -eq 1 ]

  run project_registered
  [ -z "$output" ]
}

@test "a bare repo's container is refused" {
  make_bare_container "$TMPDIR/container"
  run project_register "$TMPDIR/container"
  [ "$status" -eq 1 ]
}

@test "a worktree inside a bare-repo container is registered" {
  make_bare_container "$TMPDIR/container"
  make_repo "$TMPDIR/container/main"
  run project_register "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
}

@test "a relative path is refused" {
  run project_register "relative/path"
  [ "$status" -eq 1 ]
}

@test "a repo under an excluded prefix is refused" {
  make_repo "$WORKBENCH_STATE_DIR/reviews/wt"
  run project_register "$WORKBENCH_STATE_DIR/reviews/wt"
  [ "$status" -eq 1 ]
}

@test "a path holding the field separator is refused" {
  # The tab is what tells the path field from the repo identity, so a path
  # that carries one is indistinguishable from a line that already has one —
  # _project_contains would compare against the truncated field 1 forever and
  # every workbench command run there would append another line.
  make_repo "$TMPDIR/al"$'\t'"pha"
  run project_register "$TMPDIR/al"$'\t'"pha"
  [ "$status" -eq 1 ]

  run project_registered
  [ -z "$output" ]
}

@test "temp paths are excluded by default" {
  # The default list, not this suite's override — this is the rule that keeps
  # every other bats suite's throwaway repos out of the real registry.
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  make_repo "$TMPDIR/alpha"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 1 ]
}

@test "the default list covers the /private twin of every temp root" {
  # /tmp and /var/folders are symlinks into /private on macOS, and every caller
  # hands over a path `git rev-parse --show-toplevel` already resolved — so the
  # unprefixed spelling alone never matches what actually arrives. Asserted
  # against the predicate because a repo cannot be built outside a temp
  # directory to drive it end to end.
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  run _project_excluded /private/tmp/some-repo
  [ "$status" -eq 0 ]
  run _project_excluded /private/var/folders/xx/some-repo
  [ "$status" -eq 0 ]
  run _project_excluded /Users/someone/git/some-repo
  [ "$status" -eq 1 ]
}

@test "a state root reached through a symlink is excluded" {
  # WORKBENCH_STATE_DIR may be set to a symlink in a dotfiles-managed setup,
  # and every caller hands over a path git already resolved — so the guard has
  # to compare against the resolved spelling too, or a throwaway review
  # worktree slips into a file the machine profile renders. Mirrors
  # test_a_state_root_reached_through_a_symlink_is_excluded on the Python side.
  local real="$TMPDIR/real-state"
  local link="$TMPDIR/link-state"
  mkdir -p "$real"
  ln -s "$real" "$link"
  WORKBENCH_STATE_DIR="$link"
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  run _project_excluded "$real/reviews/wt"
  [ "$status" -eq 0 ]
}

# ─── Reads ───────────────────────────────────────────────────────────────────

@test "project_registered on a machine with no registry is empty and succeeds" {
  run project_registered
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a repo that has been deleted is skipped at read time" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"
  rm -rf "$TMPDIR/alpha"

  run project_registered
  [ "$output" = "$TMPDIR/beta" ]
}

@test "comment lines are not paths" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  printf '# a note, not a repo\n' >> "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a repo appended twice is read once" {
  # Two workbench commands starting in one repo at the same moment can each see
  # "absent" and each append — registration is guarded by a membership check,
  # not a lock. The duplicate is absorbed on read rather than paid for on every
  # write, so nothing downstream renders the repo twice.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "${#lines[@]}" -eq 1 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a final line with no newline after it is still read" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

# ─── Forget and prune ────────────────────────────────────────────────────────

@test "project_forget drops one entry and keeps the rest" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"

  run project_forget "$TMPDIR/alpha"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/beta" ]
}

@test "project_forget on an unregistered repo fails" {
  run project_forget "$TMPDIR/nowhere"
  [ "$status" -eq 1 ]
}

@test "project_prune deletes the lines project_registered was skipping" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"
  rm -rf "$TMPDIR/alpha"

  run project_prune
  [ "$output" = "1" ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_prune keeps comment lines" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  printf '# a note\n%s\n' "$TMPDIR/gone" > "$PROJECTS_REGISTRY_FILE"

  run project_prune
  [ "$output" = "1" ]
  run grep -c '^# a note' "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_prune collapses a repo appended twice" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run project_prune
  [ "$output" = "1" ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_prune on a machine with no registry reports nothing dropped" {
  run project_prune
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

# ─── Two-field lines ─────────────────────────────────────────────────────────
#
# A registry line is a work-tree path, optionally followed by a tab and the
# repo identity every worktree of that repo shares. Everything that matches a
# line compares the path ahead of the tab; the identity is data, not part of
# the name.

@test "the repo id on a line is not part of the path it names" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a repo already recorded with a repo id is not registered twice" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 3 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_forget drops a line that carries a repo id" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  printf '%s\t%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" "$TMPDIR/beta" \
    > "$PROJECTS_REGISTRY_FILE"

  run project_forget "$TMPDIR/alpha"
  [ "$status" -eq 0 ]
  run project_registered
  [ "$output" = "$TMPDIR/beta" ]
}

@test "project_prune keeps the repo id on the lines it keeps" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" "$TMPDIR/gone" \
    > "$PROJECTS_REGISTRY_FILE"

  run project_prune
  [ "$output" = "1" ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/alpha"$'\t'"$TMPDIR/alpha/.git" ]
}

@test "a repeat of a path already carrying an id is pruned" {
  # Registration is an append guarded by a membership check, so the same repo
  # can be appended twice — once before its id was recorded and once after.
  # Both name one repo, and the line holding the id is the one worth keeping.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" "$TMPDIR/alpha" \
    > "$PROJECTS_REGISTRY_FILE"

  run project_prune
  [ "$output" = "1" ]
  run cat "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "$TMPDIR/alpha"$'\t'"$TMPDIR/alpha/.git" ]
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

# ─── Cross-language agreement ────────────────────────────────────────────────

@test "the repo id bash records names the container Python resolves" {
  # lib/git_layout.py owns container resolution for Python and answers None for
  # an ordinary clone; the bash id is total and one level deeper. The two agree
  # about where a bare-repo container is, which is the half they share.
  make_bare_worktree_layout "$TMPDIR/container"

  run git_shared_dir "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
  local shared="$output"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.container_dir('$TMPDIR/container/main'))
"
  [ "$status" -eq 0 ]
  [ "$output" = "$(dirname "$shared")" ]
}

@test "bash and Python name the same registry file" {
  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print(workbench_projects.registry_path())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$PROJECTS_REGISTRY_FILE" ]
}

@test "a repo Python registered is a repo bash reads" {
  make_repo "$TMPDIR/alpha"
  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
assert workbench_projects.register('$TMPDIR/alpha')
"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a repo bash registered is a repo Python reads" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print(*workbench_projects.registered())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "bash and Python exclude the same temporary roots" {
  # Two languages spelling one membership rule — the cross-validation the SSOT
  # convention asks for when a default has to exist in both.
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  local prefix
  local -a fixed=()
  for prefix in "${PROJECTS_EXCLUDED_PREFIXES[@]}"; do
    # The rest of the list is derived from the environment, not fixed.
    if [[ "$prefix" == "${TMPDIR%/}" || "$prefix" == "$WORKBENCH_STATE_DIR" ]]; then
      continue
    fi
    if [[ "$prefix" == "$WORKBENCH_CACHE_DIR" ]]; then
      continue
    fi
    fixed+=("$prefix")
  done

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print('\n'.join(sorted(workbench_projects.TEMP_ROOTS)))
"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '%s\n' "${fixed[@]}" | sort)" ]
}

@test "both halves refuse a bare repo's container" {
  make_bare_container "$TMPDIR/container"
  run project_register "$TMPDIR/container"
  [ "$status" -eq 1 ]

  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
print(workbench_projects.register('$TMPDIR/container'))
"
  [ "$output" = "False" ]
}

@test "Python reads the path from a line bash gave a repo id" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print(*workbench_projects.registered())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "Python does not append a repo bash recorded with a repo id" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
assert workbench_projects.register('$TMPDIR/alpha')
"
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

# ─── CLI ─────────────────────────────────────────────────────────────────────

# `otto-workbench projects` is the surface an operator corrects the registry
# from — the answer to a repo that joined late or one that should never have.

@test "projects list says so when nothing is registered" {
  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "projects with no subcommand lists" {
  run "$REPO_ROOT/bin/otto-workbench" projects
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "projects list names each registered repo and counts them" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/beta" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TMPDIR/alpha"* ]]
  [[ "$output" == *"$TMPDIR/beta"* ]]
  [[ "$output" == *"2 repo(s)"* ]]
}

@test "projects list groups a repo's worktrees under it" {
  # The repos are what the count is of. A machine that cuts a worktree per
  # branch used to read as a dozen separate projects.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/container/feature" \
    > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"  $TMPDIR/container"* ]]
  [[ "$output" == *"    main"* ]]
  [[ "$output" == *"    feature"* ]]
  [[ "$output" == *"1 repo(s), 2 work tree(s)"* ]]
}

@test "projects list names an ordinary clone once" {
  # Its only work tree is the repo itself, so a line under it would repeat the
  # line above it word for word.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  run grep -c "$TMPDIR/alpha" <<< "$output"
  [ "$output" = "1" ]
}

@test "projects list writes a path under HOME with a tilde" {
  # The replacement is tilde-expanded before it is substituted in, so an
  # unescaped ~ puts $HOME back and every path printed in full.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  HOME="$TMPDIR" run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"~/alpha"* ]]
}

@test "projects add refuses a directory that is not a work tree" {
  mkdir -p "$TMPDIR/plain"
  run "$REPO_ROOT/bin/otto-workbench" projects add "$TMPDIR/plain"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Not a git work tree"* ]]
}

@test "projects add refuses a repo under a temporary path" {
  # An array cannot be exported, so the subprocess uses the real default
  # exclusion list — this is the one test that drives it end to end.
  make_repo "$TMPDIR/alpha"
  run "$REPO_ROOT/bin/otto-workbench" projects add "$TMPDIR/alpha"
  [ "$status" -eq 1 ]
  [[ "$output" == *"temporary path"* ]]

  run project_registered
  [ -z "$output" ]
}

@test "a repo that qualifies but cannot be written is not called 'not a project'" {
  # project_register returns 1 for a refusal and 2 for a write that failed. A
  # read-only state root used to read back as "a temporary path or a bare repo's
  # container", which sends the user looking at the wrong thing entirely.
  make_repo "$TMPDIR/alpha"
  mkdir -p "$WORKBENCH_STATE_DIR"
  chmod 500 "$WORKBENCH_STATE_DIR"

  run project_register "$TMPDIR/alpha"
  chmod 700 "$WORKBENCH_STATE_DIR"
  [ "$status" -eq 2 ]
}

@test "projects forget resolves a path holding .. to the stored entry" {
  # Entries are stored as `git rev-parse --show-toplevel` returned them and
  # forget matches by exact string, so anything an operator can reasonably type
  # has to be canonicalised first or a valid request reads as "not registered".
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/canon/repo"
  mkdir -p "$TMPDIR/canon/repo/sub"
  printf '%s\n' "$TMPDIR/canon/repo" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects forget "$TMPDIR/canon/repo/sub/.."
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "0" ]
}

@test "projects forget resolves .. lexically when the directory is gone" {
  # _projects_abs can't cd into a directory that no longer exists to resolve ..
  # the normal way — it has to collapse the path components itself.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/deleted-repo"
  mkdir -p "$TMPDIR/elsewhere"
  printf '%s\n' "$TMPDIR/deleted-repo" > "$PROJECTS_REGISTRY_FILE"
  rm -rf "$TMPDIR/deleted-repo"

  cd "$TMPDIR/elsewhere"
  run "$REPO_ROOT/bin/otto-workbench" projects forget "../deleted-repo"
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "0" ]
}

@test "projects forget without an argument is a usage error" {
  run "$REPO_ROOT/bin/otto-workbench" projects forget
  [ "$status" -eq 1 ]
  [[ "$output" == *"projects forget DIR"* ]]
}

@test "projects forget reports a path that was never registered" {
  run "$REPO_ROOT/bin/otto-workbench" projects forget "$TMPDIR/nowhere"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Not in the registry"* ]]
}

@test "projects prune deletes the entries list was skipping" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/gone" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pruned 1 entry(s)"* ]]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "an unknown projects subcommand prints usage and fails" {
  run "$REPO_ROOT/bin/otto-workbench" projects nonsense
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: otto-workbench projects"* ]]
}

# ─── Consumers ───────────────────────────────────────────────────────────────

@test "the context-to-architecture migration reaches a repo past the old depth limit" {
  # Six levels below the root the old `find -maxdepth 5` walked. A bare-repo
  # container sits at exactly five, so any organisation one directory deeper was
  # invisible — and the migration recorded itself applied all the same. The
  # registry answers by membership rather than by depth, and the migration is
  # handed each repo it lists.
  local deep="$TMPDIR/git/personal/otto-nation/some-repo/main/nested"
  make_repo "$deep"
  mkdir -p "$deep/.claude"
  echo "architecture" > "$deep/.claude/context.md"
  project_register "$deep"

  run project_registered
  [[ "$output" == *"$deep"* ]]

  # The framework as well as the migration: a migration's return codes are the
  # framework's vocabulary, and the twin test below asserts against one of them.
  # shellcheck source=../lib/migrations.sh
  . "$REPO_ROOT/lib/migrations.sh"
  # shellcheck source=../ai/claude/migrations/20260819-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260819-context-to-architecture.sh"
  run migration_20260819_context_to_architecture "$deep"
  [ "$status" -eq 0 ]

  [ ! -f "$deep/.claude/context.md" ]
  [ "$(cat "$deep/.claude/architecture.md")" = "architecture" ]
}

@test "the context-to-architecture migration leaves an existing architecture.md alone" {
  # No registration here, unlike the test above: what the framework hands the
  # migration is a repo path, and this one is about what the migration does with
  # the files it finds there rather than about how the path was arrived at.
  make_repo "$TMPDIR/alpha"
  mkdir -p "$TMPDIR/alpha/.claude"
  echo "old" > "$TMPDIR/alpha/.claude/context.md"
  echo "current" > "$TMPDIR/alpha/.claude/architecture.md"

  # shellcheck source=../lib/migrations.sh
  . "$REPO_ROOT/lib/migrations.sh"
  # shellcheck source=../ai/claude/migrations/20260819-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260819-context-to-architecture.sh"
  run migration_20260819_context_to_architecture "$TMPDIR/alpha"
  # MIGRATION_NOOP, not 0: leaving the file alone is the migration finding its
  # work already done, and the framework counts and reports the two apart.
  [ "$status" -eq "$MIGRATION_NOOP" ]
  [ "$(cat "$TMPDIR/alpha/.claude/architecture.md")" = "current" ]
}
