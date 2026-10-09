# Worktree launch — start an agent session in a worktree, never at a bare-repo container
#
# An agent harness roots a project at the directory the session starts in. In
# the bare-repo layout `wt-init` produces, that directory is often the
# container: it holds the bare .git and the worktrees as peers, but no working
# tree of its own. Claude Code started there sees no AGENTS.md and no .claude/
# rules. Pi started there loads context files only from the container and its
# parents — never from the worktrees below it — and none of the repo's .pi/
# settings, extensions, skills or prompts. Either way every tool call runs
# where `git status` fails. This launches the session in the worktree the
# container's default branch is checked out into instead, and says so.
#
# Only a bare-repo container is redirected. An ordinary repo, a worktree, and a
# directory outside any repo all launch exactly where you are, unchanged.
#
# The redirect is the first of two layers. A session that still starts at a
# container — `command pi`, a launcher that is not zsh — is caught by each
# harness's own backstop: ai/pi/extensions/container-context for Pi, and
# ai/claude/bin/reuse-session-start for Claude Code. All three ask the same
# resolver, `resolve-worktree`, so they cannot disagree about which worktree
# speaks for a container.
#
# No requires-cmd: the wrappers that call this (claude.zsh, pi.zsh) carry their
# own, and each sources this file by path so it works however it was loaded.
#
# Docs:            docs/architecture.md § Shell (ZSH)
# duplicate-check: ^(_wb_launch_in_worktree *\(\)|function _wb_launch_in_worktree)

# _wb_launch_in_worktree CMD [ARGS...] — run CMD with ARGS in the worktree a
# bare-repo container stands in for, or in place anywhere else. Returns CMD's
# exit status.
_wb_launch_in_worktree() {
  local cmd="$1"
  shift

  # resolve-worktree is the workbench's container resolver. Without it there is
  # nothing to resolve against, so the launch passes straight through.
  if ! command -v resolve-worktree >/dev/null 2>&1; then
    command "$cmd" "$@"
    return
  fi

  local worktree
  # Not named `status`: that is a read-only alias for `?` in zsh, and assigning
  # to it aborts the function.
  local rc=0
  # Assigned separately from the declaration: `local x="$(cmd)"` reports the
  # declaration's status, not the command's, and every failure would read as 0.
  worktree="$(resolve-worktree)" || rc=$?

  # 2 — not a bare-repo container, which is the ordinary case.
  if (( rc == 2 )); then
    command "$cmd" "$@"
    return
  fi

  # Anything else failed. resolve-worktree has already said why on stderr; name
  # the consequence rather than launching somewhere useless without a word.
  if (( rc != 0 )); then
    print -u2 -- "$cmd: cannot resolve a worktree for $PWD — launching here, where no tracked file is in scope"
    command "$cmd" "$@"
    return
  fi

  print -u2 -- "$cmd: $PWD is a bare repository — launching in $worktree"
  # A subshell, so the shell you launched from is still where you left it when
  # the session exits. Its status is the function's, so the wrapper still
  # reports what the session reported. `cd` is checked in its own subshell
  # first so a worktree that vanished between resolution and launch still
  # starts a session, in place, rather than silently returning without one.
  if ! (cd "$worktree" 2>/dev/null); then
    print -u2 -- "$cmd: cannot cd into $worktree — launching here, where no tracked file is in scope"
    command "$cmd" "$@"
    return
  fi
  (cd "$worktree" && command "$cmd" "$@")
}
