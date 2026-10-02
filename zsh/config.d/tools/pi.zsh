# Pi — launch a session in a worktree, never at a bare-repo container
#
# See _worktree_launch.zsh beside this file for what the redirect does and why;
# this file only binds it to `pi`.
#
# Pi files sessions by the directory they started in, so after the redirect
# `pi -c` run from a container continues the worktree's latest session, not one
# started at the container before this wrapper existed. Those stay reachable
# with `pi --session <path>` or `command pi -c`.
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
