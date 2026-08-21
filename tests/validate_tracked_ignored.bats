#!/usr/bin/env bats
# Tests for bin/local/validate-tracked-ignored — the gate that keeps an ignore
# entry from quietly meaning nothing.
#
# Every case runs against a fixture repo built in TMPDIR rather than the live
# checkout: the state under test is a commit that must not exist here, and the
# one assertion about the real tree is read-only.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATOR="$REPO_ROOT/bin/local/validate-tracked-ignored"
  # --exclude-standard reads core.excludesFile, so the machine's global ignore
  # would otherwise decide what these fixtures contain.
  export GIT_CONFIG_GLOBAL=/dev/null
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_repo DIR — a fixture repo holding one committed file and no ignore rules.
_make_repo() {
  local dir="$1"
  mkdir -p "$dir"
  GIT_CEILING_DIRECTORIES="$(dirname "$dir")" git -C "$dir" init --quiet
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
  git -C "$dir" config core.hooksPath /dev/null
  echo "init" > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -m "initial" --quiet
}

# _commit_file DIR RELPATH — commits a file at RELPATH before anything ignores it.
_commit_file() {
  local dir="$1" relpath="$2"
  mkdir -p "$(dirname "$dir/$relpath")"
  echo "content" > "$dir/$relpath"
  git -C "$dir" add "$relpath"
  git -C "$dir" commit -m "add $relpath" --quiet
}

# _ignore DIR ENTRY — appends an ignore entry and commits it, reproducing the
# order that leaves tracked files behind: the files first, the rule afterwards.
_ignore() {
  local dir="$1" entry="$2"
  echo "$entry" >> "$dir/.gitignore"
  git -C "$dir" add .gitignore
  git -C "$dir" commit -m "ignore $entry" --quiet
}

@test "passes when no tracked file sits under an ignored path" {
  _make_repo "$TMPDIR/repo"
  _ignore "$TMPDIR/repo" "build/"
  mkdir -p "$TMPDIR/repo/build"
  echo "artifact" > "$TMPDIR/repo/build/out.txt"

  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "no tracked file lives under an ignored path"
}

@test "fails and names a tracked file under an ignored directory" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "ai/memory/MEMORY.md"
  _ignore "$TMPDIR/repo" "ai/memory/"

  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "1 tracked file(s) live under an ignored path"
  echo "$output" | grep -q "ai/memory/MEMORY.md"
  echo "$output" | grep -q "git rm --cached"
}

@test "picks up a new ignore entry without being edited" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "dist/bundle.tar.gz"

  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 0 ]

  # The glob is a pattern this validator has never heard of — the set it checks
  # is whatever .gitignore holds when it runs, not a list kept in the script.
  _ignore "$TMPDIR/repo" "*.tar.gz"

  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "dist/bundle.tar.gz"
}

@test "counts every offending file" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "ai/memory/one.md"
  _commit_file "$TMPDIR/repo" "ai/memory/nested/two.md"
  _ignore "$TMPDIR/repo" "ai/memory/"

  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "2 tracked file(s) live under an ignored path"
  echo "$output" | grep -q "ai/memory/nested/two.md"
}

@test "reads the directory it is given, not an exported GIT_DIR" {
  _make_repo "$TMPDIR/clean"
  _make_repo "$TMPDIR/dirty"
  _commit_file "$TMPDIR/dirty" "ai/memory/MEMORY.md"
  _ignore "$TMPDIR/dirty" "ai/memory/"

  # The pre-push hook exports GIT_DIR, which outranks `git -C` — a validator
  # that let it through would read the hook's index and call the dirty repo clean.
  GIT_DIR="$TMPDIR/clean/.git" run "$VALIDATOR" "$TMPDIR/dirty"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "ai/memory/MEMORY.md"
}

@test "--quiet prints nothing on success" {
  _make_repo "$TMPDIR/repo"

  run "$VALIDATOR" --quiet "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "--quiet still reports a failure" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "ai/memory/MEMORY.md"
  _ignore "$TMPDIR/repo" "ai/memory/"

  # validate-all invokes every validator with --quiet, so a failure the flag
  # swallowed would reach the summary as a bare name and no reason.
  run "$VALIDATOR" --quiet "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "ai/memory/MEMORY.md"
}

@test "--help prints usage and exits 0" {
  run "$VALIDATOR" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage: validate-tracked-ignored"
}

@test "rejects an unknown flag with exit 2" {
  run "$VALIDATOR" --nope
  [ "$status" -eq 2 ]
  echo "$output" | grep -q "Unknown argument: --nope"
}

@test "rejects a second directory with exit 2" {
  run "$VALIDATOR" "$TMPDIR" "$TMPDIR"
  [ "$status" -eq 2 ]
  echo "$output" | grep -q "Only one directory may be given"
}

@test "the workbench checkout has no tracked file under an ignored path" {
  run "$VALIDATOR" "$REPO_ROOT"
  [ "$status" -eq 0 ]
}
