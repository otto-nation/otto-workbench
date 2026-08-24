#!/usr/bin/env bats
# Tests for generate-anatomy.sh — file index generation and large-repo coverage.

bats_require_minimum_version 1.5.0

# The 60x40 tree three cases below assert against, generated once.
#
# Building it costs about two seconds in file creation alone and another in
# `git add`, and the three cases that want it read three different parts of one
# document rather than exercising three generator runs — so they share a run.
# The per-test REPO is untouched: cases that need their own fixture still get a
# clean one from setup().
setup_file() {
  load 'test_helper'
  # Not just setup()'s job: the pre-push hook exports GIT_DIR, and every git
  # call below would target the real repo without this — `git -C "$BIG_REPO"
  # config user.email test@example.com` writes to the workbench's own config.
  common_setup
  BIG_TMPDIR="$BATS_FILE_TMPDIR/big"
  BIG_REPO="$BIG_TMPDIR/repo"
  mkdir -p "$BIG_REPO/.claude"
  _make_dirs_in "$BIG_REPO" 60 40
  _init_repo_at "$BIG_REPO"
  bash "$REPO_ROOT/ai/claude/skills/anatomy/generate-anatomy.sh" "$BIG_REPO" >/dev/null
  BIG_ANATOMY="$BIG_REPO/.claude/anatomy.md"
  export BIG_REPO BIG_ANATOMY
}

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  TMPDIR="$(mktemp -d)"
  GEN_ANATOMY="$REPO_ROOT/ai/claude/skills/anatomy/generate-anatomy.sh"
  REPO="$TMPDIR/repo"
  mkdir -p "$REPO/.claude"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _init_repo_at DIR — makes DIR a git repo with one commit covering everything present
_init_repo_at() {
  git -C "$1" init -q --initial-branch=main
  git -C "$1" config user.email test@example.com
  git -C "$1" config user.name Test
  git -C "$1" add -A
  git -C "$1" commit -qm init
}

# _init_repo — _init_repo_at against the per-test $REPO.
_init_repo() {
  _init_repo_at "$REPO"
}

# _make_dirs_in DIR COUNT FILES_PER_DIR — creates COUNT dirs of FILES_PER_DIR files each
_make_dirs_in() {
  local root="$1" count="$2" per="$3" i j
  for ((i = 0; i < count; i++)); do
    local dir
    dir="$(printf '%s/area-%03d' "$root" "$i")"
    mkdir -p "$dir"
    for ((j = 0; j < per; j++)); do
      printf '# area %03d file %d\ncode\n' "$i" "$j" > "$(printf '%s/f%02d.sh' "$dir" "$j")"
    done
  done
}

# _make_dirs COUNT FILES_PER_DIR — _make_dirs_in against the per-test $REPO.
_make_dirs() {
  _make_dirs_in "$REPO" "$1" "$2"
}

@test "generates an index with per-file rows and descriptions" {
  printf '#!/usr/bin/env bash\n# Does a useful thing\ncode\n' > "$REPO/tool.sh"
  mkdir -p "$REPO/lib"
  printf '"""Library docstring."""\n' > "$REPO/lib/mod.py"
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  run cat "$REPO/.claude/anatomy.md"
  [[ "$output" == *"# Project Anatomy"* ]]
  [[ "$output" == *"Does a useful thing"* ]]
  [[ "$output" == *"Library docstring."* ]]
}

@test "falls back to a filename label when no comment is found" {
  printf 'plain content\n' > "$REPO/some_data_file.txt"
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]
  grep -q 'Some data file' "$REPO/.claude/anatomy.md"
}

@test "small repos get no Directory Index section" {
  _make_dirs 3 2
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]
  run grep -c 'Directory Index' "$REPO/.claude/anatomy.md"
  [ "$output" -eq 0 ]
}

# 60 dirs x 40 files = 2400 files, past the 2000-file detail budget. The three
# cases below read the shared run from setup_file.
@test "repos over MAX_FILES still list every directory" {
  grep -q '## Directory Index' "$BIG_ANATOMY"

  # Every area must appear, whether detailed or indexed. Before the Directory
  # Index existed, the alphabetical truncation dropped the tail outright.
  local i missing=0
  for ((i = 0; i < 60; i++)); do
    local area
    area="$(printf 'area-%03d' "$i")"
    grep -q "$area/" "$BIG_ANATOMY" || { echo "missing: $area"; missing=1; }
  done
  [ "$missing" -eq 0 ]
}

@test "a single directory larger than MAX_FILES is indexed, not detailed" {
  # One dir of 2100 files exceeds the 2000-file budget on its own. The budget is
  # a bound on the whole document, so this directory gets an index row only.
  _make_dirs 1 2100
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  grep -q '^| area-000/ | 2100 |' "$REPO/.claude/anatomy.md"
  run grep -c '^| f[0-9]' "$REPO/.claude/anatomy.md"
  [ "$output" -eq 0 ]

  run head -2 "$REPO/.claude/anatomy.md"
  [[ "$output" == *"detailed: 0 in 0 dirs"* ]]
  [[ "$output" == *"indexed: 1 dirs"* ]]
}

@test "header reports detailed and indexed counts separately" {
  run head -2 "$BIG_ANATOMY"
  [[ "$output" == *"files: 2400"* ]]
  [[ "$output" == *"detailed:"* ]]
  [[ "$output" == *"indexed:"* ]]
}

@test "directory index rows carry file and line totals" {
  # Each generated file is 2 lines, so an indexed area-NNN row reads "| 40 | 80 |".
  grep -qE '^\| area-[0-9]{3}/ \| 40 \| 80 \| 320 \|$' "$BIG_ANATOMY"
}

@test "skips binary extensions and lockfiles" {
  printf 'binary\n' > "$REPO/logo.png"
  printf '{}\n' > "$REPO/package-lock.json"
  printf '# real\ncode\n' > "$REPO/real.sh"
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]
  run cat "$REPO/.claude/anatomy.md"
  [[ "$output" != *"logo.png"* ]]
  [[ "$output" != *"package-lock.json"* ]]
  [[ "$output" == *"real.sh"* ]]
}

@test "is a no-op when the stored git hash matches HEAD" {
  printf '# thing\ncode\n' > "$REPO/a.sh"
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  local before after
  before="$(file_mtime "$REPO/.claude/anatomy.md")"
  sleep 1
  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  after="$(file_mtime "$REPO/.claude/anatomy.md")"
  [ "$before" -eq "$after" ]
}

@test "exits quietly when the repo has no .claude directory" {
  rmdir "$REPO/.claude"
  printf '# thing\n' > "$REPO/a.sh"
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]
  [ ! -e "$REPO/.claude/anatomy.md" ]
}

# ── Bare-repo containers ─────────────────────────────────────────────────────
# The container holds .claude/ but no source; the worktree beside it holds the
# source. resolve-worktree is what joins the two, so it has to be on PATH.

@test "indexes the resolved worktree when run at a bare container" {
  printf '# a thing\ncode\n' > "$REPO/a.sh"
  _init_repo

  local container="$TMPDIR/container"
  mkdir -p "$container/.claude"
  git clone -q --bare "$REPO" "$container/.git"
  git -C "$container" worktree add -q "$container/main" main

  PATH="$REPO_ROOT/bin:$PATH" run bash "$GEN_ANATOMY" "$container"
  [ "$status" -eq 0 ]
  [ -f "$container/.claude/anatomy.md" ]
  [[ "$(cat "$container/.claude/anatomy.md")" == *"a.sh"* ]]
}

@test "exits quietly at a bare container with no worktree to index" {
  printf '# a thing\ncode\n' > "$REPO/a.sh"
  _init_repo

  local container="$TMPDIR/container"
  mkdir -p "$container/.claude"
  git clone -q --bare "$REPO" "$container/.git"

  PATH="$REPO_ROOT/bin:$PATH" run bash "$GEN_ANATOMY" "$container"
  [ "$status" -eq 0 ]
  [ ! -e "$container/.claude/anatomy.md" ]
}
