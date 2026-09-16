#!/usr/bin/env bash
# checkout-scoped: the files live under each work tree's own .workbench/, so
# having removed them is a fact about that checkout. A machine-wide line would
# skip every work tree registered after the first sync, and a repo-scoped line
# would skip every extra work tree of the same repo — both leave the litter
# in place.
#
# Not adoption-sensitive: this removes rather than seeds. A .workbench/
# arriving later is a fresh install whose contents the operator chose.
#
# Migration: remove the state.json, run.lock, and trail.jsonl the pre-target
# layout left in a worktree. The directory itself goes only when nothing else
# is in it — an entry this layout never wrote is not ours to delete.

migration_20260916_remove_legacy_worktree_state() {
  local work_tree="$1"
  local legacy removed=0 name path

  # workbench_paths.LEGACY_WORKTREE_STATE_DIRNAME — not in lib/constants.sh.
  legacy="$work_tree/.workbench"
  [[ -d "$legacy" ]] || return "$MIGRATION_NOOP"

  # pr_state.STATE_FILE, run_lock.LOCK_FILE, and the pre-cutover trail name
  # that used to live as _LEGACY_TRAIL_FILENAME in ai/bin/pr. None of the
  # three is exposed to bash; trail.py no longer names the file.
  for name in state.json run.lock trail.jsonl; do
    path="$legacy/$name"
    if [[ -f "$path" ]]; then
      rm -f "$path"
      removed=1
    fi
  done

  if (( removed == 0 )); then
    return "$MIGRATION_NOOP"
  fi

  rmdir "$legacy" 2>/dev/null || true
  return 0
}
