#!/usr/bin/env bash
# The bare-repo worktree layout, as git names it — bash half.
#
# A repository is one thing and its checkouts are several: `wt-init` and
# `worktrunk` produce a container holding a bare `.git` with every worktree as a
# peer, and a worktree comes and goes with `wt switch` and `wt remove` while the
# repository stays. Anything recorded about the repository rather than about one
# branch therefore needs a name the checkouts share, and
# `git rev-parse --git-common-dir` is it: every worktree of one repository
# resolves it to the same directory, and two repositories never share one.
#
# ```bash
# . "$LIB_SRC_DIR/git_layout.sh"
# git_shared_dir /path/to/worktree     # → /path/to/container/.git
# ```
#
# `lib/git_layout.py` is the Python half and answers the
# neighbouring question — the *container*, the shared git dir's parent, and
# deliberately nothing for an ordinary clone whose parent belongs to somebody
# else. This one is total, because the caller here wants an identity rather than
# a directory to write into, and `/repo/.git` is a perfectly good identity.
# `tests/projects_agreement.bats` cross-validates the two.
#
# `bin/resolve-worktree` owns the other direction — container → the worktree it
# stands in for.
#
# The hooks in `git/hooks/` ask the same question of their own repository and
# deliberately do not come here. `git_shared_dir` clears the git environment and
# re-discovers from a directory, while the repository a hook answers for is the
# one git named in the environment it exported: `git --git-dir=X --work-tree=Y
# commit` runs a hook in Y with `GIT_DIR` set to X, and discovery from Y finds no
# repository at all — or, worse, whichever one happens to enclose it. A hook is
# the one caller that must leave the environment alone, so it asks
# `git rev-parse --git-common-dir` itself.

[[ -n "${_LIB_GIT_LAYOUT_SH:-}" ]] && return
_LIB_GIT_LAYOUT_SH=1

# Resolved from this file's own location rather than $LIB_SRC_DIR, the same way
# lib/setup.sh resolves its own sibling sources — see the note above
# _projects_lib_dir in lib/projects.sh for why.
_git_layout_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gitenv.sh
. "$_git_layout_lib_dir/gitenv.sh"
# The container resolver git_bare_container asks, beside this lib in the same
# checkout so the two always agree on what a container is.
_GIT_LAYOUT_RESOLVE_WORKTREE="$_git_layout_lib_dir/../bin/resolve-worktree"
unset _git_layout_lib_dir

# git_shared_dir DIR — the shared git directory DIR's repository uses, resolved
# to a physical path. Non-zero and silent when git cannot answer for DIR.
#
# The whole lookup is a subshell, which is what scopes the `cd` and the cleared
# git environment to it. GIT_DIR beats `git -C`, so a sync run from inside a git
# hook — pre-push exports one — would otherwise be answered for the hook's
# repository, and the answer would look entirely ordinary.
#
# `cd` into what `--git-common-dir` reports rather than joining it onto DIR:
# git may answer with a path relative to the working directory, and `pwd -P`
# then resolves both that and any symlink in it the same way the callers'
# paths were resolved.
git_shared_dir() {
  local dir="$1"
  (
    git_env_clear
    cd "$dir" 2>/dev/null || exit 1
    local common
    common="$(git rev-parse --git-common-dir 2>/dev/null)" || exit 1
    cd "$common" 2>/dev/null || exit 1
    pwd -P
  )
}

# git_bare_container DIR — the bare-repo container DIR's repository sits in,
# printed; non-zero and silent when DIR is not in that layout.
#
# The container is the shared git dir's parent, but only a bare-repo layout has
# one: an ordinary clone's parent is a directory that belongs to somebody else,
# and writing a config file there rewrites whatever lives beside the clone.
# `bin/resolve-worktree` owns "is this a container" in bash — 0 for one with a
# worktree on its default branch, 1 for one without, both of which can hold a
# config file; 2 for an ordinary repo or worktree and 64 for a path that is
# gone, neither of which is ever answered with a container.
#
# The git environment is cleared for resolve-worktree too: it discovers from
# the directory it is handed, and GIT_DIR exported by a git hook would make it
# answer for the hook's repository.
git_bare_container() {
  local dir="$1" common container rc=0
  common="$(git_shared_dir "$dir")" || return 1
  container="$(dirname "$common")"
  ( git_env_clear; "$_GIT_LAYOUT_RESOLVE_WORKTREE" "$container" >/dev/null 2>&1 ) || rc=$?
  (( rc == 0 || rc == 1 )) || return 1
  printf '%s' "$container"
}
