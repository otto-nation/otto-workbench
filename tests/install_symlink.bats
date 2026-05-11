#!/usr/bin/env bats
# Regression tests for the ln -sfh fix in install_symlink.
# Previously, `ln -sf` on macOS (BSD ln) would dereference an existing symlink
# at the destination — corrupting repo files (file symlinks) or creating nested
# symlinks (directory symlinks) on re-runs of install.sh.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  # Source install_symlink
  source "$REPO_ROOT/lib/files.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  unset WORKBENCH_DIR WORKBENCH_STABLE_DIR
  common_teardown
}

# ── File symlink re-run ───────────────────────────────────────────────────────

# ── Worktree-stable symlinks ──────────────────────────────────────────────────

@test "install_symlink rewrites source to WORKBENCH_STABLE_DIR when set" {
  mkdir -p "$TMPDIR/feature-branch" "$TMPDIR/main"
  echo "content" > "$TMPDIR/main/script"

  WORKBENCH_DIR="$TMPDIR/feature-branch"
  WORKBENCH_STABLE_DIR="$TMPDIR/main"

  install_symlink "$TMPDIR/feature-branch/script" "$TMPDIR/link"

  [ -L "$TMPDIR/link" ]
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/main/script" ]
}

@test "install_symlink does not rewrite when WORKBENCH_STABLE_DIR equals WORKBENCH_DIR" {
  mkdir -p "$TMPDIR/repo"
  echo "content" > "$TMPDIR/repo/script"

  WORKBENCH_DIR="$TMPDIR/repo"
  WORKBENCH_STABLE_DIR="$TMPDIR/repo"

  install_symlink "$TMPDIR/repo/script" "$TMPDIR/link"

  [ -L "$TMPDIR/link" ]
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/repo/script" ]
}

@test "install_symlink replaces stale worktree symlink with stable one on re-run" {
  mkdir -p "$TMPDIR/old-wt" "$TMPDIR/main"
  echo "content" > "$TMPDIR/main/script"
  echo "content" > "$TMPDIR/old-wt/script"

  # First run: old symlink pointing to a different worktree
  ln -s "$TMPDIR/old-wt/script" "$TMPDIR/link"

  WORKBENCH_DIR="$TMPDIR/current-wt"
  WORKBENCH_STABLE_DIR="$TMPDIR/main"

  install_symlink "$TMPDIR/current-wt/script" "$TMPDIR/link"

  [ -L "$TMPDIR/link" ]
  [ "$(readlink "$TMPDIR/link")" = "$TMPDIR/main/script" ]
}

# ── BSD ln regression tests (macOS only) ─────────────────────────────────────

@test "re-running ln -sfh on an existing file symlink replaces the symlink, not the file" {
  [[ "$OSTYPE" == "darwin"* ]] || skip "macOS only"
  echo "original" > "$TMPDIR/file.txt"
  ln -s "$TMPDIR/file.txt" "$TMPDIR/link"

  # Simulate what install_symlink does on a second run
  ln -sfh "$TMPDIR/file.txt" "$TMPDIR/link"

  # The link must still be a symlink, not a regular file
  [ -L "$TMPDIR/link" ]
  # The original file must still have its content (not been replaced by a symlink)
  [ -f "$TMPDIR/file.txt" ]
  [ "$(cat "$TMPDIR/file.txt")" = "original" ]
}

# ── Directory symlink re-run ──────────────────────────────────────────────────

@test "re-running ln -sfh on an existing directory symlink replaces the symlink, not nesting inside" {
  [[ "$OSTYPE" == "darwin"* ]] || skip "macOS only"
  mkdir "$TMPDIR/source_dir"
  ln -s "$TMPDIR/source_dir" "$TMPDIR/link_dir"

  # Simulate what install_symlink does on a second run (e.g. lib/ → ~/.config/task/lib)
  ln -sfh "$TMPDIR/source_dir" "$TMPDIR/link_dir"

  # No nested symlink must have been created inside source_dir
  [ ! -e "$TMPDIR/source_dir/source_dir" ]
  [ ! -L "$TMPDIR/source_dir/source_dir" ]
  # The outer symlink must still exist
  [ -L "$TMPDIR/link_dir" ]
}
