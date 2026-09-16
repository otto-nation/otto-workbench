#!/usr/bin/env bash
set -e
# checkout-scoped: the file being moved is inside a work tree, and the target it
# moves to is keyed on that work tree's origin and branch — so the state line
# belongs to the work tree and not to the shared git dir.
#
# Migration: carry <work-tree>/ignore/pr-comments/state.json to the run target's
# pr-comments/state.json under the workbench state root.
#
# The review-thread ledger was the last file the comments pass wrote inside the
# repo it was operating on. A target repo that does not gitignore `ignore/`
# tracked it, and it is scratch state rather than anything the repo should hold.
#
# Moved, not copied, and not dropped. Most of what the ledger holds is re-fetched
# from the API every run, but `classification`, `summary` and `decided_at` are
# triage decisions made locally and no API call reproduces them — abandoning the
# file would silently re-triage every open thread on every PR the machine has
# one for.
#
# Where it lands is derived by `pr.target`, not rebuilt here: the key is a
# canonicalised origin plus a digest of it, and a second implementation of that
# in bash would put the carried file somewhere the tool does not look — which
# reads exactly like the ledger having been lost.

migration_20260915_pr_comments_state_root() {
  local work_tree="$1"
  local old_file="$work_tree/ignore/pr-comments/state.json"

  # NOOP rather than DEFERRED: a work tree with no old file either never ran the
  # comments pass or has already been carried, and neither becomes untrue later.
  # The pass writes to the new location now, so nothing recreates this path.
  [[ -f "$old_file" ]] || return "$MIGRATION_NOOP"

  # $WORKBENCH_DIR's own ai/lib, by path: this runs as part of that tree's sync,
  # so the key must be the one that tree's `pr` will look under.
  local target_dir
  target_dir=$(python3 - "$WORKBENCH_DIR" "$work_tree" <<'PY' || true
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target

resolved = pr_target.target_dir_for_checkout(Path(sys.argv[2]))
print(resolved or "")
PY
  )

  # No key means no destination: a work tree with no `origin`, or on a detached
  # HEAD, is one `pr` itself reports as having no state. The `|| true` above
  # folds a failed resolution into the same empty answer on purpose — both mean
  # there is nowhere to put the file, and neither is a reason to take the rest
  # of the sync down. Left in place and not recorded, so a repo that gains a
  # remote is carried by the next sync rather than having been quietly retired
  # against a path nothing read.
  if [[ -z "$target_dir" ]]; then
    warn "Could not key $work_tree to a run target — leaving its thread ledger in place"
    return 1
  fi

  local new_file="$target_dir/pr-comments/state.json"

  # The new file wins, and the old one goes. Both present means a run has
  # already written the new location since the old file was last touched, so
  # the old one is staler than what would overwrite — and leaving it behind is
  # how the same work tree is visited again on the next machine's sync.
  if [[ -f "$new_file" ]]; then
    rm -f "$old_file"
    _prune_pr_comments_dirs "$work_tree"
    success "Dropped a superseded thread ledger in $work_tree"
    return 0
  fi

  mkdir -p "$target_dir/pr-comments"
  # mv, not cp: two ledgers for one PR is not a backup, it is a second answer to
  # "what was decided", and only one of them is the one the tool reads.
  mv "$old_file" "$new_file"
  _prune_pr_comments_dirs "$work_tree"
  success "Carried the thread ledger from $work_tree to $new_file"
}

# The directories the ledger used to live in, removed only while empty. `ignore/`
# is a real directory in some repos holding plans and specs, and rmdir refusing
# a non-empty one is what keeps this from touching them.
_prune_pr_comments_dirs() {
  local work_tree="$1"
  rmdir "$work_tree/ignore/pr-comments" 2>/dev/null || true
  rmdir "$work_tree/ignore" 2>/dev/null || true
}
