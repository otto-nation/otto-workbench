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

@test "select_menu: out-of-range indices are ignored" {
  result=$(_select "1 9" 3 --default skip)
  [ "$result" = "1" ]
}

@test "select_menu: --single stops after first valid entry" {
  result=$(_select "2 3" 3 --default skip --single)
  [ "$result" = "2" ]
}

@test "select_menu: duplicate indices are preserved as entered" {
  result=$(_select "1 1" 2 --default skip)
  [ "$result" = "1 1" ]
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
