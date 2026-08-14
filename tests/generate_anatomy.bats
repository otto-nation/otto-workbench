#!/usr/bin/env bats
# Tests for generate-anatomy.sh — file index generation and large-repo coverage.

bats_require_minimum_version 1.5.0

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

# _init_repo — makes $REPO a git repo with one commit covering everything present
_init_repo() {
  git -C "$REPO" init -q
  git -C "$REPO" config user.email test@example.com
  git -C "$REPO" config user.name Test
  git -C "$REPO" add -A
  git -C "$REPO" commit -qm init
}

# _make_dirs COUNT FILES_PER_DIR — creates COUNT dirs of FILES_PER_DIR files each
_make_dirs() {
  local count="$1" per="$2" i j
  for ((i = 0; i < count; i++)); do
    local dir
    dir="$(printf '%s/area-%03d' "$REPO" "$i")"
    mkdir -p "$dir"
    for ((j = 0; j < per; j++)); do
      printf '# area %03d file %d\ncode\n' "$i" "$j" > "$(printf '%s/f%02d.sh' "$dir" "$j")"
    done
  done
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

@test "repos over MAX_FILES still list every directory" {
  # 60 dirs x 40 files = 2400 files, past the 2000-file detail budget.
  _make_dirs 60 40
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  grep -q '## Directory Index' "$REPO/.claude/anatomy.md"

  # Every area must appear, whether detailed or indexed. Before the Directory
  # Index existed, the alphabetical truncation dropped the tail outright.
  local i missing=0
  for ((i = 0; i < 60; i++)); do
    local area
    area="$(printf 'area-%03d' "$i")"
    grep -q "$area/" "$REPO/.claude/anatomy.md" || { echo "missing: $area"; missing=1; }
  done
  [ "$missing" -eq 0 ]
}

@test "header reports detailed and indexed counts separately" {
  _make_dirs 60 40
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  run head -2 "$REPO/.claude/anatomy.md"
  [[ "$output" == *"files: 2400"* ]]
  [[ "$output" == *"detailed:"* ]]
  [[ "$output" == *"indexed:"* ]]
}

@test "directory index rows carry file and line totals" {
  _make_dirs 60 40
  _init_repo

  run bash "$GEN_ANATOMY" "$REPO"
  [ "$status" -eq 0 ]

  # Each generated file is 2 lines, so an indexed area-NNN row reads "| 40 | 80 |".
  grep -qE '^\| area-[0-9]{3}/ \| 40 \| 80 \| 320 \|$' "$REPO/.claude/anatomy.md"
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
