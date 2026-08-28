#!/usr/bin/env bash
# repo-scoped: the anatomy index belongs to the container every worktree of the
# repo shares, so being done is a fact about that repo rather than about any one
# checkout of it.
# Migration: delete the anatomy index a bare-repo container was given while the
# generator still wrote one there.
#
# The index described a worktree's files under a root where none of those paths
# exist, and nothing ever read it. A bare repo has no work tree, so no
# .gitignore rule, review, or CI check reaches inside a container — a wrong file
# written there can only be deleted by hand, which is what this does instead.
#
# The index is all that goes. The container's .claude/ belongs to the permission
# mirror, which writes a generated settings.json into it on every sync.
#
# Repo-scoped rather than a single machine-wide sweep so a repo the machine
# learns about later is still visited, and rather than checkout-scoped because
# every worktree of one container would otherwise be visited for one deletion
# and hold a state line for it. The framework hands over one of the repo's
# registered work trees, which is all the function needs — it resolves the
# container from there.

# _drop_container_anatomy_container DIR — the bare-repo container DIR sits in,
# or a non-zero status when DIR is not in that layout.
#
# The shared git dir comes from lib/git_layout.sh, which owns that lookup and
# clears the git environment inside its own subshell. The clear is repeated here
# for resolve-worktree below: it discovers from the directory it is handed, and a
# sync run from inside a git hook has GIT_DIR exported — which git reads ahead of
# any directory, so the answer would be the hook's repository and would look
# entirely ordinary. Called through a command substitution, which is what scopes
# the clear to the lookup.
_drop_container_anatomy_container() {
  local dir="$1" common container rc=0

  git_env_clear

  common="$(git_shared_dir "$dir")" || return 1
  container="$(dirname "$common")"

  # resolve-worktree owns "is this a bare-repo container" in bash. 0 is a
  # container with a worktree on its default branch and 1 is one whose default
  # branch has no worktree — both still hold whatever was written into them. 2
  # says it is an ordinary repo or worktree, which is where the parent of a
  # non-bare git dir lands, and 64 says the path is gone. Only 2 and 64 mean
  # there is nothing here to clean, and mistaking either for a container would
  # delete a real checkout's tracked anatomy.md.
  "$BIN_SRC_DIR/resolve-worktree" "$container" >/dev/null 2>&1 || rc=$?
  (( rc == 0 || rc == 1 )) || return 1

  printf '%s' "$container"
}

migration_20260824_drop_container_anatomy() {
  local project_dir="$1" container stale

  container="$(_drop_container_anatomy_container "$project_dir")" \
    || return "$MIGRATION_NOOP"

  stale="$container/.claude/anatomy.md"
  [[ -f "$stale" ]] || return "$MIGRATION_NOOP"

  rm -f "$stale"
  success "Removed the stale anatomy index at $stale"
}
