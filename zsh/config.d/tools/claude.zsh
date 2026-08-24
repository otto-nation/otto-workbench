# Claude Code — launch a session in a worktree, never at a bare-repo container
#
# Claude Code roots a project at the directory the session starts in. In the
# bare-repo layout `wt-init` produces, that directory is often the container:
# it holds the bare .git and the worktrees as peers, but no working tree of its
# own. A session started there sees no CLAUDE.md, no .claude/ rules, and no
# source — everything that defines the project lives one level down. This
# launches the session in the worktree the container's default branch is
# checked out into instead, and says so.
#
# Only a bare-repo container is redirected. An ordinary repo, a worktree, and a
# directory outside any repo all launch exactly where you are, unchanged.
#
# ceiling-permanent: `command claude`, `\claude`, and an absolute path to the
# binary all bypass this function, so the redirect is a default rather than a
# guarantee. The only way to close it is a PATH shim ahead of the real binary,
# which would take away the escape hatch a deliberate container-rooted session
# needs and break `command claude` for every caller that relies on it.
#
# Install:         https://claude.com/claude-code
# Docs:            docs/architecture.md § Shell (ZSH)
# duplicate-check: ^(alias claude=|claude *\(\)|function claude)
# requires-cmd:    claude

claude() {
  # resolve-worktree is the workbench's container resolver. Without it there is
  # nothing to resolve against, so the launch passes straight through.
  if ! command -v resolve-worktree >/dev/null 2>&1; then
    command claude "$@"
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
    command claude "$@"
    return
  fi

  # Anything else failed. resolve-worktree has already said why on stderr; name
  # the consequence rather than launching somewhere useless without a word.
  if (( rc != 0 )); then
    print -u2 -- "claude: cannot resolve a worktree for $PWD — launching here, where no tracked file is in scope"
    command claude "$@"
    return
  fi

  print -u2 -- "claude: $PWD is a bare repository — launching in $worktree"
  # A subshell, so the shell you launched from is still where you left it when
  # the session exits. Its status is the function's, so `claude` still reports
  # what the session reported. `cd` is checked in its own subshell first so a
  # worktree that vanished between resolution and launch still starts a
  # session, in place, rather than silently returning without one.
  if ! (cd "$worktree" 2>/dev/null); then
    print -u2 -- "claude: cannot cd into $worktree — launching here, where no tracked file is in scope"
    command claude "$@"
    return
  fi
  (cd "$worktree" && command claude "$@")
}
