# Claude Code — launch a session in a worktree, never at a bare-repo container
#
# See _worktree_launch.zsh beside this file for what the redirect does and why;
# this file only binds it to `claude`.
#
# ceiling-permanent: `command claude`, `\claude`, and an absolute path to the
# binary all bypass this function, so the redirect is a default rather than a
# guarantee. The only way to close it is a PATH shim ahead of the real binary,
# which would take away the escape hatch a deliberate container-rooted session
# needs and break `command claude` for every caller that relies on it. A bypass
# is not silent: ai/claude/bin/reuse-session-start says when a session started
# at a container.
#
# Install:         https://claude.com/claude-code
# Docs:            docs/architecture.md § Shell (ZSH)
# duplicate-check: ^(alias claude=|claude *\(\)|function claude)
# requires-cmd:    claude

source "${${(%):-%x}:h}/_worktree_launch.zsh"

claude() {
  _wb_launch_in_worktree claude "$@"
}
