#!/usr/bin/env bash
# The inherited git environment, and how to stop it choosing the repository.
#
# git reads GIT_DIR ahead of the directory `-C` moved to, and GIT_INDEX_FILE
# ahead of the index inside it. A script that takes a repository path from its
# caller is therefore answered by whatever repository the environment names, not
# by the one it was given — and the answer looks entirely ordinary. The pre-push
# hook exports GIT_DIR, so every gate under `bin/local/` that accepts a path runs
# in exactly that situation.
#
# It has no dependencies, so a caller that has not loaded the facade can source
# it on its own:
#
# ```bash
# git_env_clear          # then `git -C DIR ...` really means DIR
# ```

[[ -n "${_LIB_GITENV_SH:-}" ]] && return
_LIB_GITENV_SH=1

# git_env_clear — drop every git environment override inherited from a caller,
# so that `git -C DIR` and repository discovery both answer for DIR.
#
# One list rather than a copy per gate: which variables have to go is a single
# fact about git, and a variable found to leak later is then fixed everywhere at
# once. Clearing is also what restores discovery for a caller that passes no
# directory at all — with GIT_DIR set, `git rev-parse --show-toplevel` answers
# the caller's cwd instead of the repo root.
git_env_clear() {
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
}
