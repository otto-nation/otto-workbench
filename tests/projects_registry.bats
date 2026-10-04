#!/usr/bin/env bats
# Tests for the project registry in lib/projects.sh — registration, reads, forget and prune.
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
