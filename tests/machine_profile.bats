#!/usr/bin/env bats
# Tests for ai/claude/skills/machine/generate-machine-profile.sh — how it names
# the workbench's own location, which used to be a list of three hardcoded
# candidate paths, and how it names the repos on the machine, which used to be a
# `find` over four guessed-at git roots.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # A test's repos are all temporary, which is what the default exclusion list
  # refuses; the sandboxed state root keeps the writes out of the real registry.
  # shellcheck disable=SC2034  # read by lib/projects.sh, sourced through ui.sh
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

@test "the machine profile names a workbench location that really exists" {
  # The three hardcoded candidates could promise no such thing: each was a guess
  # at where the repo might be, and a machine that cloned it anywhere else got a
  # profile with the line silently missing.
  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]

  local named
  named="$(sed -n 's|^- otto-workbench: ||p' "$TMPDIR/home/.claude/machine/machine.md")"
  [ -n "$named" ]
  [ -f "$named/lib/constants.sh" ]
}

@test "the machine profile reports an unresolvable workbench location" {
  # An empty workbench_dir used to drop the line entirely, with nothing anywhere
  # reporting the miss. It is now visible in the profile and on stderr.
  export WORKBENCH_STABLE_DIR="$TMPDIR/no-such-workbench"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"did not resolve"* ]]
  grep -q '^- otto-workbench: location unresolved' "$TMPDIR/home/.claude/machine/machine.md"
}

@test "the machine profile lists the registered repos" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q "| alpha | $TMPDIR/alpha |" "$TMPDIR/home/.claude/machine/machine.md"
}

# ─── The Issues column ───────────────────────────────────────────────────────
#
# Resolved through the typed loader as the table is written, so the registry —
# machine-local and built from observed use — holds no copy that could disagree
# with the repo, and so the column cannot disagree with the SessionStart line
# either. Every scope the loader reads is a scope this column reports: a value
# the repo declared reads bare, one it only inherits from the machine is tagged,
# and nothing at all reads "unset".

# profile_row NAME — the registry row for the repo called NAME.
profile_row() {
  grep "^| $1 |" "$TMPDIR/home/.claude/machine/machine.md"
}

@test "the machine profile reads each repo's issue tracker from the repo itself" {
  make_repo "$TMPDIR/alpha"
  printf 'issue_tracker:\n  provider: github\n' > "$TMPDIR/alpha/.workbench.yml"
  project_register "$TMPDIR/alpha"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q '^| Project | Path | Stack | Issues | Memory |$' "$TMPDIR/home/.claude/machine/machine.md"
  [[ "$(profile_row alpha)" == *"| github |"* ]]
}

@test "the machine profile reads a tracker recorded above a repo's worktrees" {
  # The bug the delegation exists to fix. A bare-repo container holds the answer
  # for every worktree of the repo, none of which has a .workbench.yml of its
  # own — so a reader of the project file alone called all of them unset while
  # the SessionStart line in the same session named the tracker correctly.
  mkdir -p "$TMPDIR/seed"
  printf 'seed\n' > "$TMPDIR/seed/README.md"
  make_container_seed "$TMPDIR/seed"
  make_worktree_container "$TMPDIR/container" "$TMPDIR/seed"
  printf 'issue_tracker:\n  provider: linear\n' > "$TMPDIR/container/.workbench.yml"
  project_register "$TMPDIR/container/main"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  # The repo's row, not the checkout's: the tracker is the repository's answer,
  # and the container is where it was recorded.
  [[ "$(profile_row container)" == *"| linear |"* ]]
  grep -q "^| ↳ main | $TMPDIR/container/main |" "$TMPDIR/home/.claude/machine/machine.md"
}

@test "the machine profile tags a tracker the repo only inherits" {
  # A machine-wide default is an answer, so the row is not "unset" — but it is
  # not the repo's answer either, and a reader deciding whether the repo still
  # owes one has to be able to tell the two apart.
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf 'issue_tracker:\n  provider: github\n' > "$WORKBENCH_CONFIG_FILE"
  make_repo "$TMPDIR/zeta"
  project_register "$TMPDIR/zeta"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row zeta)" == *"| github (global) |"* ]]
}

@test "the machine profile renders an undeclared issue tracker as unset" {
  # Not a guess at Linear, and not the "—" the Stack column uses for none: an
  # undeclared tracker is a question still owed an answer, and this table is
  # where the repos owing one are meant to be visible.
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/beta"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row beta)" == *"| unset |"* ]]
}

@test "the machine profile degrades an unreadable repo config to unset" {
  # A config nobody can parse is one row's problem. The repo listed beside it is
  # what proves the degrade is scoped rather than a render that gave up.
  make_repo "$TMPDIR/gamma"
  printf 'issue_tracker:\n  provider: [unclosed\n' > "$TMPDIR/gamma/.workbench.yml"
  project_register "$TMPDIR/gamma"
  make_repo "$TMPDIR/delta"
  printf 'issue_tracker:\n  provider: linear\n' > "$TMPDIR/delta/.workbench.yml"
  project_register "$TMPDIR/delta"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row gamma)" == *"| unset |"* ]]
  [[ "$(profile_row delta)" == *"| linear |"* ]]
}

@test "the machine profile keeps a value that cannot fill a table cell out of the row" {
  # yq parses this fine, so the malformed-file path above never sees it — but
  # the pipes would split the row into columns the header has no names for and
  # garble every repo listed after it.
  make_repo "$TMPDIR/epsilon"
  printf 'issue_tracker:\n  provider: "a | b"\n' > "$TMPDIR/epsilon/.workbench.yml"
  project_register "$TMPDIR/epsilon"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row epsilon)" == *"| unset |"* ]]
}

# ─── Grouping ────────────────────────────────────────────────────────────────
#
# One line per work tree is what the registry holds; one row per repository with
# its checkouts under it is what the table is read for. A machine that cuts a
# worktree per branch had fifteen rows saying the same repo's name.

@test "the machine profile puts a repo's worktrees under one row" {
  mkdir -p "$TMPDIR/seed"
  printf 'seed\n' > "$TMPDIR/seed/README.md"
  make_container_seed "$TMPDIR/seed"
  make_worktree_container "$TMPDIR/container" "$TMPDIR/seed"
  git --git-dir="$TMPDIR/container/.git" worktree add -b feature \
    "$TMPDIR/container/feature" >/dev/null 2>&1
  project_register "$TMPDIR/container/main"
  project_register "$TMPDIR/container/feature"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  local profile="$TMPDIR/home/.claude/machine/machine.md"
  [ "$(grep -c '^| container |' "$profile")" = "1" ]
  grep -q "^| ↳ main | $TMPDIR/container/main | | |" "$profile"
  grep -q "^| ↳ feature | $TMPDIR/container/feature | | |" "$profile"
  grep -q '^_A `↳` row is a work tree of the repo above it' "$profile"
}

@test "the machine profile reads a worktree repo's stack from a checkout" {
  # The container holds no source files of its own, so a table that asked it
  # what the repo was built in answered "—" for every repo with worktrees.
  mkdir -p "$TMPDIR/seed"
  printf '{}\n' > "$TMPDIR/seed/package.json"
  make_container_seed "$TMPDIR/seed"
  make_worktree_container "$TMPDIR/container" "$TMPDIR/seed"
  project_register "$TMPDIR/container/main"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row container)" == *"| node |"* ]]
}

@test "the machine profile finds the memories a worktree repo keeps at its container" {
  # Claude records a session started in a worktree under the container, so a
  # table that only asked the checkouts reported "no" for every one of them
  # while the memories sat one level up.
  mkdir -p "$TMPDIR/seed"
  printf 'seed\n' > "$TMPDIR/seed/README.md"
  make_container_seed "$TMPDIR/seed"
  make_worktree_container "$TMPDIR/container" "$TMPDIR/seed"
  project_register "$TMPDIR/container/main"
  mkdir -p "$TMPDIR/home/.claude/projects/${TMPDIR//\//-}-container/memory"
  printf 'a fact\n' > "$TMPDIR/home/.claude/projects/${TMPDIR//\//-}-container/memory/one.md"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$(profile_row container)" == *"| yes (1 files) |"* ]]
}

@test "the machine profile says so when nothing is registered" {
  # The heading used to be inside the conditional, so an empty list took the
  # whole section with it and the profile read as though the machine had no
  # repos rather than as though nothing had registered yet.
  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q '^## Project Registry' "$TMPDIR/home/.claude/machine/machine.md"
  grep -q 'No repos registered yet' "$TMPDIR/home/.claude/machine/machine.md"
}
