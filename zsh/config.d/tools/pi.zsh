# Pi — launch a session in a worktree, never at a bare-repo container
#
# See _worktree_launch.zsh beside this file for what the redirect does and why;
# this file only binds it to `pi`.
#
# Pi files sessions by the directory they started in (docs/sessions.md in the
# Pi 1.0.0 package, the version this was written against: `--continue` opens
# the most recent session for the current working directory, sessions are
# stored grouped by working directory, `--session` takes a path or ID). So
# after the redirect `pi -c` run from a container continues the worktree's
# latest session, not one started at the container before this wrapper
# existed. Those stay reachable with `pi --session <path>` or `command pi -c`.
# Read from Pi's docs, not exercised here; recheck on a Pi upgrade.
#
# ceiling-permanent: `command pi`, `\pi`, and an absolute path to the binary all
# bypass this function, so the redirect is a default rather than a guarantee.
# A PATH shim ahead of the real binary would close it, at the cost of the escape
# hatch a deliberate container-rooted session needs. A bypass is not silent:
# ai/pi/extensions/container-context loads the worktree's context file into
# such a session and says what it could not load.
#
# Install:         https://github.com/earendil-works/pi
# Docs:            docs/architecture.md § Shell (ZSH)
# duplicate-check: ^(alias pi=|pi *\(\)|function pi)
# requires-cmd:    pi

source "${${(%):-%x}:h}/_worktree_launch.zsh"

pi() {
  _wb_launch_in_worktree pi "$@"
}
