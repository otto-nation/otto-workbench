# herdr — agent multiplexer: persistent panes and agent state for Pi and Claude Code
#
# Loads shell completions for the `herdr` CLI.
# Not deployed until herdr is installed; re-run: otto-workbench sync zsh
#
# Install:         otto-workbench install ai  (select herdr)
# Docs:            https://herdr.dev/docs/
# duplicate-check: herdr completion
# requires-cmd:    herdr

[[ -x "$(command -v herdr 2>/dev/null)" ]] || return 0

source <(herdr completion zsh)
