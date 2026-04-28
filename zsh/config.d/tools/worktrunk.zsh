# worktrunk — git worktree manager for parallel development and AI agent workflows
#
# Activates shell integration (cd-on-switch, completions) for the `wt` CLI.
# Not deployed until worktrunk is installed; re-run: otto-workbench sync zsh
#
# Install:         brew install worktrunk
# Docs:            https://worktrunk.dev
# duplicate-check: wt config shell
# requires-cmd:    wt

[[ -x "$(command -v wt 2>/dev/null)" ]] || return 0

eval "$(wt config shell init zsh)"
