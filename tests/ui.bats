#!/usr/bin/env bats
# Tests for interactive helpers and symlink utilities in lib/ui.sh.

setup() {
  load 'test_helper'
  TMPDIR="$(mktemp -d)"
  export NO_COLOR=1
  # Source lib/ui.sh so all helpers are available in test process
  # shellcheck source=../lib/ui.sh
  source "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  unset SYMLINK_MODE
}

# ── select_menu ───────────────────────────────────────────────────────────────
# select_menu uses `read` so tests run it in an isolated subshell with mocked stdin.

# _select INPUT COUNT [OPTIONS...] — runs select_menu in a subshell, prints result.
_select() {
  local input="$1"; shift
  local args="$*"
  bash -c "
    export NO_COLOR=1
    source '$REPO_ROOT/lib/ui.sh'
    select_menu _sel $args 2>/dev/null
    echo \"\$_sel\"
  " <<< "$input" 2>/dev/null | tail -n1
}

@test "select_menu: empty input with --default all selects every index" {
  result=$(_select "" 3 --default all)
  [ "$result" = "1 2 3" ]
}

@test "select_menu: empty input with --default skip returns empty" {
  result=$(_select "" 3 --default skip)
  [ -z "$result" ]
}

@test "select_menu: 0 always skips regardless of default" {
  result=$(_select "0" 3 --default all)
  [ -z "$result" ]
}

@test "select_menu: specific indices are returned" {
  result=$(_select "1 3" 3 --default skip)
  [ "$result" = "1 3" ]
}

@test "select_menu: out-of-range index re-prompts then accepts valid input" {
  result=$(_select $'1 9\n2' 3 --default skip)
  [ "$result" = "2" ]
}

@test "select_menu: non-numeric input re-prompts then accepts valid input" {
  result=$(_select $'abc\n1 3' 3 --default skip)
  [ "$result" = "1 3" ]
}

@test "select_menu: re-prompt accepts 0 to skip" {
  result=$(_select $'xyz\n0' 3 --default skip)
  [ -z "$result" ]
}

@test "select_menu: --single stops after first valid entry" {
  result=$(_select "2 3" 3 --default skip --single)
  [ "$result" = "2" ]
}

@test "select_menu: duplicate indices are preserved as entered" {
  result=$(_select "1 1" 2 --default skip)
  [ "$result" = "1 1" ]
}

# ── select_subdirs ────────────────────────────────────────────────────────────

# _select_subdirs INPUT PARENT_DIR [OPTIONS...] — runs select_subdirs in a subshell, prints result.
_select_subdirs() {
  local input="$1" parent_dir="$2"
  shift 2
  local args="$*"
  bash -c "
    export NO_COLOR=1
    source '$REPO_ROOT/lib/ui.sh'
    select_subdirs _sel '$parent_dir' 'Pick one' $args 2>/dev/null
    echo \"\$_sel\"
  " <<< "$input" 2>/dev/null | tail -n1
}

@test "select_subdirs: maps index to directory name" {
  mkdir -p "$TMPDIR/parent/alpha" "$TMPDIR/parent/beta"
  echo "#!/bin/bash" > "$TMPDIR/parent/alpha/setup.sh"
  echo "#!/bin/bash" > "$TMPDIR/parent/beta/setup.sh"
  result=$(_select_subdirs "1" "$TMPDIR/parent" --single)
  [ "$result" = "alpha" ]
}

@test "select_subdirs: caller variable _sel receives result (nameref regression)" {
  mkdir -p "$TMPDIR/parent/alpha" "$TMPDIR/parent/beta"
  echo "#!/bin/bash" > "$TMPDIR/parent/alpha/setup.sh"
  echo "#!/bin/bash" > "$TMPDIR/parent/beta/setup.sh"
  # This test uses _sel as the result variable — the exact pattern that was
  # broken when select_subdirs used printf -v with its own local _sel.
  result=$(_select_subdirs "2" "$TMPDIR/parent" --single)
  [ "$result" = "beta" ]
}

# ── confirm ───────────────────────────────────────────────────────────────────

@test "confirm: y returns success" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm 'msg'" <<< "y"
  [ "$status" -eq 0 ]
}

@test "confirm: Y returns success" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm 'msg'" <<< "Y"
  [ "$status" -eq 0 ]
}

@test "confirm: n returns failure" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm 'msg'" <<< "n"
  [ "$status" -eq 1 ]
}

@test "confirm: N returns failure" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm 'msg'" <<< "N"
  [ "$status" -eq 1 ]
}

@test "confirm: empty input defaults to yes" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm 'msg'" <<< ""
  [ "$status" -eq 0 ]
}

# ── confirm_n ─────────────────────────────────────────────────────────────────

@test "confirm_n: y returns success" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm_n 'msg'" <<< "y"
  [ "$status" -eq 0 ]
}

@test "confirm_n: Y returns success" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm_n 'msg'" <<< "Y"
  [ "$status" -eq 0 ]
}

@test "confirm_n: n returns failure" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm_n 'msg'" <<< "n"
  [ "$status" -eq 1 ]
}

@test "confirm_n: empty input defaults to no" {
  run bash -c "source '$REPO_ROOT/lib/ui.sh'; confirm_n 'msg'" <<< ""
  [ "$status" -eq 1 ]
}

# ── install_symlink ───────────────────────────────────────────────────────────

@test "install_symlink: creates symlink pointing to source" {
  echo "content" > "$TMPDIR/src"
  install_symlink "$TMPDIR/src" "$TMPDIR/link"
  [ -L "$TMPDIR/link" ]
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/src" ]
}

@test "install_symlink: is idempotent — running twice leaves symlink unchanged" {
  echo "content" > "$TMPDIR/src"
  install_symlink "$TMPDIR/src" "$TMPDIR/link"
  install_symlink "$TMPDIR/src" "$TMPDIR/link"
  [ -L "$TMPDIR/link" ]
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/src" ]
}

@test "install_symlink: updates a symlink pointing to a different source" {
  echo "a" > "$TMPDIR/src_a"
  echo "b" > "$TMPDIR/src_b"
  install_symlink "$TMPDIR/src_a" "$TMPDIR/link"
  install_symlink "$TMPDIR/src_b" "$TMPDIR/link"
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/src_b" ]
}

@test "install_symlink: SYMLINK_MODE=no-prompt does not overwrite a real file" {
  echo "real" > "$TMPDIR/target"
  echo "source" > "$TMPDIR/src"
  SYMLINK_MODE=no-prompt install_symlink "$TMPDIR/src" "$TMPDIR/target"
  [ ! -L "$TMPDIR/target" ]
  [ "$(cat "$TMPDIR/target")" = "real" ]
}

@test "install_symlink: SYMLINK_MODE=no-prompt warns about the real file" {
  echo "real" > "$TMPDIR/target"
  echo "source" > "$TMPDIR/src"
  SYMLINK_MODE=no-prompt run install_symlink "$TMPDIR/src" "$TMPDIR/target"
  [[ "$output" == *"real file"* ]]
}

# ── symlink_dir --prune ───────────────────────────────────────────────────────

@test "symlink_dir --prune removes stale symlinks pointing into src" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/keep.zsh"
  echo "b" > "$TMPDIR/src/remove.zsh"
  symlink_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh"
  [ -L "$TMPDIR/dst/keep.zsh" ]
  [ -L "$TMPDIR/dst/remove.zsh" ]

  rm "$TMPDIR/src/remove.zsh"
  symlink_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh" --prune

  [ -L "$TMPDIR/dst/keep.zsh" ]
  [ ! -e "$TMPDIR/dst/remove.zsh" ]
  [ ! -L "$TMPDIR/dst/remove.zsh" ]
}

@test "symlink_dir --prune keeps valid symlinks pointing into src" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/file.zsh"
  symlink_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh"
  symlink_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh" --prune
  [ -L "$TMPDIR/dst/file.zsh" ]
}

@test "symlink_dir --prune does not remove symlinks pointing outside src" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst" "$TMPDIR/other"
  echo "external" > "$TMPDIR/other/external.zsh"
  ln -s "$TMPDIR/other/external.zsh" "$TMPDIR/dst/external.zsh"

  symlink_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh" --prune

  [ -L "$TMPDIR/dst/external.zsh" ]
}

# ── install_file ──────────────────────────────────────────────────────────────

@test "install_file: copies source to target" {
  echo "content" > "$TMPDIR/src"
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  [ -f "$TMPDIR/dst" ]
  [ "$(cat "$TMPDIR/dst")" = "content" ]
}

@test "install_file: is idempotent — no-op when content is identical" {
  echo "content" > "$TMPDIR/src"
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  # Backdate dst so a re-copy would visibly change the mtime (no sleep needed)
  touch -t 200001010000 "$TMPDIR/dst"
  local mtime1
  mtime1=$(stat -f "%m" "$TMPDIR/dst" 2>/dev/null || stat -c "%Y" "$TMPDIR/dst")
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  local mtime2
  mtime2=$(stat -f "%m" "$TMPDIR/dst" 2>/dev/null || stat -c "%Y" "$TMPDIR/dst")
  [ "$mtime1" = "$mtime2" ]
}

@test "install_file: updates target when content differs" {
  echo "old" > "$TMPDIR/src"
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  echo "new" > "$TMPDIR/src"
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  [ "$(cat "$TMPDIR/dst")" = "new" ]
}

@test "install_file: replaces a stale symlink at target" {
  echo "src" > "$TMPDIR/src"
  echo "other" > "$TMPDIR/other"
  ln -s "$TMPDIR/other" "$TMPDIR/dst"
  install_file "$TMPDIR/src" "$TMPDIR/dst"
  [ ! -L "$TMPDIR/dst" ]
  [ "$(cat "$TMPDIR/dst")" = "src" ]
}

# ── copy_dir ──────────────────────────────────────────────────────────────────

@test "copy_dir: copies matching files into dst" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/a.zsh"
  echo "b" > "$TMPDIR/src/b.zsh"
  copy_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh"
  [ -f "$TMPDIR/dst/a.zsh" ]
  [ -f "$TMPDIR/dst/b.zsh" ]
}

@test "copy_dir: does not create symlinks — copies real files" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/a.zsh"
  copy_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh"
  [ ! -L "$TMPDIR/dst/a.zsh" ]
}

@test "copy_dir: --prune removes stale files in dst" {
  mkdir -p "$TMPDIR/src" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/keep.zsh"
  echo "b" > "$TMPDIR/src/remove.zsh"
  copy_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh"
  rm "$TMPDIR/src/remove.zsh"
  copy_dir "$TMPDIR/src" "$TMPDIR/dst" "*.zsh" --prune
  [ -f "$TMPDIR/dst/keep.zsh" ]
  [ ! -e "$TMPDIR/dst/remove.zsh" ]
}

@test "copy_dir: handles src with trailing slash" {
  mkdir -p "$TMPDIR/src/" "$TMPDIR/dst"
  echo "a" > "$TMPDIR/src/a.zsh"
  copy_dir "$TMPDIR/src/" "$TMPDIR/dst" "*.zsh"
  [ -f "$TMPDIR/dst/a.zsh" ]
}
