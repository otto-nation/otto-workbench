#!/usr/bin/env bash
# checkout-scoped: the plans and specs live under each work tree's own
# `ignore/`, so having moved them is a fact about that checkout. A machine-wide
# line would skip every work tree registered after the first sync, and a
# repo-scoped one would skip every extra work tree of the same repo — either
# leaves the documents where `wt remove` will take them.
#
# Not adoption-sensitive: this relocates rather than seeds. An `ignore/plans/`
# appearing later is a fresh write the operator chose, and by then the guards
# refuse one anyway.
#
# Migration: move a work tree's ignore/plans and ignore/specs to the
# repository's workspace at the container, where they survive `wt remove` and
# are visible from every sibling checkout.
#
# Several work trees can hold a file of the same name, so a name already taken
# at the destination is left where it is and reported rather than overwritten:
# two branches' notes under one filename are two documents, and this is not the
# place to decide which survives. Directories are left behind for the same
# reason — `rmdir` only, so anything unmoved keeps its home.

# _move_unless_taken SRC DEST MOVED_REF CLASHED_REF
#
# Its own function so the caller's loop stays within the nesting budget, and
# because "what happens to one file" is the decision worth naming: a name
# already at the destination is two branches' notes under one filename, which
# is two documents. Deciding which survives is not this migration's to make.
_move_unless_taken() {
  local src="$1" dest="$2"
  local -n __u_moved="$3" __u_clashed="$4"

  if [[ -e "$dest" ]]; then
    warn "  $(basename "$dest") already in the workspace — left in $(dirname "$src")"
    __u_clashed=$(( __u_clashed + 1 ))
    return 0
  fi

  mv "$src" "$dest"
  __u_moved=$(( __u_moved + 1 ))
}

# _move_kind SRC DEST MOVED_REF CLASHED_REF — every entry of SRC into DEST.
#
# `dotglob` so a leading-dot file is carried too, and `nullglob` so an empty
# directory iterates zero times rather than once over the literal pattern.
# Both are saved and restored: they are shell-wide, and the sync sourcing this
# migration has its own globbing to do afterwards.
_move_kind() {
  local src="$1" dest="$2"
  local -n __k_moved="$3" __k_clashed="$4"
  local path had_dotglob had_nullglob

  had_dotglob=$(shopt -p dotglob)
  had_nullglob=$(shopt -p nullglob)
  shopt -s dotglob nullglob

  for path in "$src"/*; do
    _move_unless_taken "$path" "$dest/$(basename "$path")" __k_moved __k_clashed
  done

  eval "$had_dotglob"
  eval "$had_nullglob"
}

migration_20260930_plans_specs_to_workspace() {
  local work_tree="$1"
  local workspace kind src dest moved=0 clashed=0

  [[ -d "$work_tree/ignore/plans" || -d "$work_tree/ignore/specs" ]] \
    || return "$MIGRATION_NOOP"

  # The resolver owns the path, and its refusal is honoured here too: an
  # ordinary clone has no container, so there is nowhere to move these to.
  # Left in place rather than relocated somewhere this migration invented.
  workspace="$("$BIN_SRC_DIR/resolve-workspace" "$work_tree" 2>/dev/null)" || {
    warn "No container for $work_tree — leaving ignore/plans and ignore/specs in place"
    return "$MIGRATION_NOOP"
  }

  # A per-entry move rather than `mv src/* dest/`, so each name can be tested
  # at the destination before it is taken.
  for kind in plans specs; do
    src="$work_tree/ignore/$kind"
    [[ -d "$src" ]] || continue
    dest="$workspace/$kind"
    mkdir -p "$dest"
    _move_kind "$src" "$dest" moved clashed
    rmdir "$src" 2>/dev/null || true
  done

  rmdir "$work_tree/ignore" 2>/dev/null || true

  if (( moved == 0 && clashed == 0 )); then
    return "$MIGRATION_NOOP"
  fi
  info "Moved $moved plan/spec file(s) to $workspace"
  return 0
}
