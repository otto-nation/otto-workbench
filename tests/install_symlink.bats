#!/usr/bin/env bats
# Regression tests for the ln -sfh fix in install_symlink.
# Previously, `ln -sf` on macOS (BSD ln) would dereference an existing symlink
# at the destination — corrupting repo files (file symlinks) or creating nested
# symlinks (directory symlinks) on re-runs of install.sh.

setup() {
  TMPDIR="$(mktemp -d)"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ── File symlink re-run ───────────────────────────────────────────────────────

@test "re-running ln -sfh on an existing file symlink replaces the symlink, not the file" {
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
