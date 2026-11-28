#!/usr/bin/env bash
# Migration: move retro and promote reports off the workbench checkout onto
# the data root. Reports written into WORKBENCH_DIR/ai/memory landed in
# whichever worktree sourced constants.sh (usually main/).
#
# Not adoption-sensitive: this relocates rather than seeds. A leftover
# ai/memory/RETRO.md appearing later is a stray write the new constants no
# longer produce.

# _move_report SRC DEST MOVED_REF — mv unless DEST exists. Missing SRC is a
# no-op. A name already at the destination is left where it is: the living
# report at the new path is the one should-retro.sh will read.
_move_report() {
  local src="$1" dest="$2"
  local -n __r_moved="$3"

  [[ -f "$src" ]] || return 0
  if [[ -e "$dest" ]]; then
    warn "  $(basename "$dest") already at $dest — left in $src"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  mv "$src" "$dest"
  __r_moved=$(( __r_moved + 1 ))
}

# _move_archive_files SRC_DIR DEST_DIR MOVED_REF — every *.md in the old
# archive directory. Same clash rule as _move_report.
_move_archive_files() {
  local src_dir="$1" dest_dir="$2" moved_ref="$3"
  local path had_nullglob

  [[ -d "$src_dir" ]] || return 0
  had_nullglob=$(shopt -p nullglob)
  shopt -s nullglob
  for path in "$src_dir"/*.md; do
    _move_report "$path" "$dest_dir/$(basename "$path")" "$moved_ref"
  done
  eval "$had_nullglob"
  rmdir "$src_dir" 2>/dev/null || true
}

migration_20261128_retro_promote_to_data_root() {
  local src_root moved=0
  src_root="${WORKBENCH_STABLE_DIR:-$WORKBENCH_DIR}/ai/memory"

  [[ -d "$src_root" ]] || return "$MIGRATION_NOOP"

  _move_report "$src_root/RETRO.md" "$RETRO_DIR/RETRO.md" moved
  _move_archive_files "$src_root/retro" "$RETRO_ARCHIVE_DIR" moved
  _move_report "$src_root/PROMOTE.md" "$PROMOTE_DIR/PROMOTE.md" moved

  (( moved > 0 )) || return "$MIGRATION_NOOP"
  return 0
}
