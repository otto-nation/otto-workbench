#!/usr/bin/env bash
set -e
# repo-scoped: authored memory lives under the data root, keyed per repo.
# A clone registered after the first sync — including a checkout that moved —
# still needs its leftover path-keyed directory carried onto the origin key.
# The framework records that against the shared git dir, so a late registration
# is a new target rather than a skip.
#
# Identity used to be the shared git dir, so a clone that moved got a new key
# and dream skipped it (the new directory did not exist) while the old one
# sat as an orphan. Origin is stable across that move; this run carries the
# live repo's old directory onto that key, then adopts a leftover whose slug
# still uniquely names this repo when it has no directory yet.

migration_20261125_memory_key_origin() {
  local repo_dir="$1"
  # Sourced here rather than at file scope: the framework sources every
  # migration file to discover its function, so a file-scope source runs on
  # every migration run including the ones that never call this.
  # shellcheck source=../../../lib/ai/session-count.sh
  . "$LIB_SRC_DIR/ai/session-count.sh"

  if ! declare -F _repo_key _repo_key_from_shared_dir >/dev/null; then
    warn "lib/ai/session-count.sh did not define the key helpers — retrying next run"
    return 1
  fi

  [[ -d "$WORKBENCH_MEMORY_DIR" ]] || return "$MIGRATION_NOOP"
  [[ -n "$repo_dir" ]] || return "$MIGRATION_NOOP"

  local moved=0
  _memory_rekey_one "$repo_dir" moved || return 1
  _memory_adopt_for "$repo_dir" moved || return 1
  if [[ "$moved" -gt 0 ]]; then
    return 0
  fi
  return "$MIGRATION_NOOP"
}

# _memory_rekey_one DIR MOVED — rename DIR's shared-dir key onto its origin
# key when the origin directory is still absent. After a move this only
# sees the *new* shared git dir; leftovers from the old path are adopted
# below by walking the memory root.
_memory_rekey_one() {
  local repo_dir="$1"
  local -n __moved=$2
  local old new
  new="$(_repo_key "$repo_dir")" || return 0
  old="$(_repo_key_from_shared_dir "$repo_dir")" || return 0
  [[ "$old" != "$new" ]] || return 0
  [[ -d "$WORKBENCH_MEMORY_DIR/$old" ]] || return 0
  [[ -e "$WORKBENCH_MEMORY_DIR/$new" ]] && return 0
  if ! mv "$WORKBENCH_MEMORY_DIR/$old" "$WORKBENCH_MEMORY_DIR/$new"; then
    warn "Could not rename $old to $new — left it in place"
    return 1
  fi
  __moved=$((__moved + 1))
  return 0
}

# _memory_adopt_for DIR MOVED — give a leftover path-keyed directory to DIR
# when it uniquely carries DIR's basename and DIR has no memory directory
# yet. Two leftovers sharing the name are left alone. Matching is on the
# basename as a hyphen-delimited token, not on `--git--`, because a 64-
# character slug truncation often drops that infix.
_memory_adopt_for() {
  local repo_dir="$1"
  local -n __moved=$2
  local dest name
  dest="$(_repo_key "$repo_dir")" || return 0
  [[ -d "$WORKBENCH_MEMORY_DIR/$dest" ]] && return 0
  name="$(basename "$repo_dir")"
  [[ -n "$name" && "$name" != git ]] || return 0
  _memory_adopt_one "$name" "$dest" moved
}

# _memory_adopt_one NAME DEST MOVED — move the single leftover matching
# NAME onto DEST.
_memory_adopt_one() {
  local name="$1" dest="$2"
  local -n __moved=$3
  local dir key
  local -a candidates=()
  for dir in "$WORKBENCH_MEMORY_DIR"/*; do
    [[ -d "$dir" ]] || continue
    key="${dir##*/}"
    [[ "$key" == "$dest" ]] && continue
    _memory_key_names "$key" "$name" || continue
    candidates+=("$dir")
  done
  [[ "${#candidates[@]}" -eq 1 ]] || return 0
  if ! mv "${candidates[0]}" "$WORKBENCH_MEMORY_DIR/$dest"; then
    warn "Could not adopt ${candidates[0]##*/} onto $dest — left it in place"
    return 1
  fi
  __moved=$((__moved + 1))
  return 0
}

# _memory_key_names KEY NAME — true when KEY's slug still carries NAME as a
# hyphen-delimited token (the leftover from a path-keyed directory).
_memory_key_names() {
  local key="$1" name="$2"
  [[ "$key" == *"-${name}-"* || "$key" == *"-${name}" ]]
}
