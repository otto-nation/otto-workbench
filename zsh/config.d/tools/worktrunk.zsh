# worktrunk — git worktree manager for parallel development and AI agent workflows
#
# Activates shell integration (cd-on-switch, completions) for the `wt` CLI.
# No-op if worktrunk is not installed.
#
# Install:         brew install worktrunk
# Docs:            https://worktrunk.dev
# duplicate-check: wt config shell

[[ -x "$(command -v wt 2>/dev/null)" ]] || return 0

eval "$(wt config shell show zsh)"
