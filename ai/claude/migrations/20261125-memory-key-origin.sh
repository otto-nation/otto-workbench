#!/usr/bin/env bash
set -e
# Migration: rename path-keyed memory directories onto hosted-origin keys.
#
# Machine-scoped: authored memory lives under the data root, not in a checkout.
# Identity used to be the shared git dir, so a clone that moved got a new key
# and dream skipped it (the new directory did not exist) while the old one
# sat as an orphan. Origin is stable across that move; this run carries each
# live repo's old directory onto that key, then adopts a leftover whose slug
# still uniquely names a registered repo that has no directory yet.

migration_20261125_memory_key_origin() {
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

  local moved=0
  _memory_rekey_live_repos moved || return 1
  _memory_adopt_unique_orphans moved || return 1
  if [[ "$moved" -gt 0 ]]; then
    return 0
  fi
  return "$MIGRATION_NOOP"
}

# _memory_rekey_live_repos MOVED — rename each registered repo's shared-dir
# key onto its origin key when the origin directory is still absent.
_memory_rekey_live_repos() {
  local -n __moved=$1
  local line id worktree repo_dir old new
  # shellcheck disable=SC2034
  while IFS= read -r line; do
    _split_repo_worktree_line "$line" id worktree
    repo_dir="$(project_repo_label "$id")"
    [[ -n "$repo_dir" ]] || continue
    new="$(_repo_key "$repo_dir")" || continue
    old="$(_repo_key_from_shared_dir "$repo_dir")" || continue
    [[ "$old" != "$new" ]] || continue
    [[ -d "$WORKBENCH_MEMORY_DIR/$old" ]] || continue
    [[ -e "$WORKBENCH_MEMORY_DIR/$new" ]] && continue
    if ! mv "$WORKBENCH_MEMORY_DIR/$old" "$WORKBENCH_MEMORY_DIR/$new"; then
      warn "Could not rename $old to $new — left it in place"
      return 1
    fi
    __moved=$((__moved + 1))
  done < <(project_repo_leaders)
  return 0
}

# _memory_adopt_unique_orphans MOVED — give a leftover path-keyed directory
# to the one registered repo whose basename it still carries, when that repo
# has no memory directory yet. Two leftovers or two repos sharing a name are
# left alone.
_memory_adopt_unique_orphans() {
  local -n __moved=$1
  # worktree is written by the nameref split and deliberately not read.
  # shellcheck disable=SC2034
  local line id worktree repo_dir dest name
  local -A dest_for_name=()
  local -A name_ambiguous=()
  local -A live=()

  # shellcheck disable=SC2034
  while IFS= read -r line; do
    _split_repo_worktree_line "$line" id worktree
    repo_dir="$(project_repo_label "$id")"
    dest="$(_repo_key "$repo_dir")" || continue
    live[$dest]=1
    [[ -d "$WORKBENCH_MEMORY_DIR/$dest" ]] && continue
    name="$(basename "$repo_dir")"
    [[ -n "$name" && "$name" != git ]] || continue
    if [[ -n "${dest_for_name[$name]:-}" && "${dest_for_name[$name]}" != "$dest" ]]; then
      name_ambiguous[$name]=1
    fi
    dest_for_name[$name]="$dest"
  done < <(project_repo_leaders)

  for name in "${!dest_for_name[@]}"; do
    [[ -z "${name_ambiguous[$name]:-}" ]] || continue
    dest="${dest_for_name[$name]}"
    [[ -d "$WORKBENCH_MEMORY_DIR/$dest" ]] && continue
    _memory_adopt_one "$name" "$dest" live moved || return 1
  done
  return 0
}

# _memory_adopt_one NAME DEST LIVE MOVED — move the single leftover matching
# NAME onto DEST. Split out of _memory_adopt_unique_orphans to stay inside
# the two-level nesting limit.
_memory_adopt_one() {
  local name="$1" dest="$2"
  local -n __live=$3
  local -n __moved=$4
  local dir key
  local -a candidates=()
  for dir in "$WORKBENCH_MEMORY_DIR"/*; do
    [[ -d "$dir" ]] || continue
    key="${dir##*/}"
    [[ -z "${__live[$key]:-}" ]] || continue
    [[ "$key" == *"-${name}--git--"* ]] || continue
    candidates+=("$dir")
  done
  [[ "${#candidates[@]}" -eq 1 ]] || return 0
  if ! mv "${candidates[0]}" "$WORKBENCH_MEMORY_DIR/$dest"; then
    warn "Could not adopt ${candidates[0]##*/} onto $dest — left it in place"
    return 1
  fi
  __live["$dest"]=1
  __moved=$((__moved + 1))
  return 0
}
