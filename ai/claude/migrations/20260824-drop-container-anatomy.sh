#!/usr/bin/env bash
# project-scoped: a container is only reachable from a repo inside it, and being
# done is a fact about that container rather than about the machine.
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
# Project-scoped rather than a single machine-wide sweep so a container the
# machine learns about later is still visited. Containers are shared, so the
# first of a container's worktrees to be visited does the deletion and every
# later one answers MIGRATION_NOOP.

# _drop_container_anatomy_container DIR — the bare-repo container DIR sits in,
# or a non-zero status when DIR is not in that layout.
#
# Called through a command substitution, which is what scopes its `cd` and its
# cleared git environment to the lookup. GIT_DIR beats `git -C`, so a sync run
# from inside a git hook would otherwise answer for the hook's repository.
_drop_container_anatomy_container() {
  local dir="$1" common container rc=0

  git_env_clear

  cd "$dir" 2>/dev/null || return 1
  common="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
  container="$(cd "$(dirname "$common")" 2>/dev/null && pwd -P)" || return 1

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
