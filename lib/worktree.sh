#!/usr/bin/env bash
# Where a project artifact goes.
#
# A file that lives inside a repository — `.claude/anatomy.md`, `.mcp.json`, a
# `CLAUDE.md` — belongs in a working tree. A bare-repo container has none: it
# holds the bare `.git` plus each checkout as a peer, so a file written at the
# container root is tracked by nothing, covered by no `.gitignore` rule, and
# reached by no review or CI check. The only way one is ever found is by hand.
#
# Every writer therefore resolves its tree before writing rather than trusting
# the current directory, and does it the same way:
#
# ```bash
# root="$(project_root)" || rc=$?
# ```

[[ -n "${_LIB_WORKTREE_SH:-}" ]] && return
_LIB_WORKTREE_SH=1

# project_root [DIR] — prints the working tree DIR belongs in, for a caller
# about to write a file that lives inside a repository. DIR defaults to the
# current directory. Exits 0 with the path, 1 when DIR is a bare-repo container
# naming no worktree to write into, 2 when DIR is in no repository at all, and
# 64 when DIR does not exist.
#
# The two answers come from different places on purpose. A working tree names
# itself, and `git rev-parse --show-toplevel` is how it does so; only when there
# is none does bin/resolve-worktree — the single owner of container→worktree
# resolution in bash — get asked, and its exit codes are passed straight through
# so a caller switches on one set of meanings rather than two.
#
# What a caller does with 2 is its own decision: a Stop hook has nothing to
# refresh, a scaffolder has a plain directory to scaffold. 1 is the case this
# exists for, and no caller may treat it as "write here instead" — the current
# directory in that case *is* the container.
#
# Both lookups run in a subshell with the inherited git environment cleared. A
# GIT_DIR set by a git hook beats `git -C` and makes `--show-toplevel` answer the
# caller's cwd, which is exactly the wrong answer to give quietly; clearing it in
# a subshell keeps the caller's own environment as it found it.
project_root() {
  local dir="${1:-$PWD}" root rc=0

  root="$(git_env_clear; git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || root=""
  if [[ -n "$root" ]]; then
    printf '%s\n' "$root"
    return 0
  fi

  root="$(git_env_clear; "$BIN_SRC_DIR/resolve-worktree" "$dir")" || rc=$?
  (( rc == 0 )) || return "$rc"

  printf '%s\n' "$root"
}
