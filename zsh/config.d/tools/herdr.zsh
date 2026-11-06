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

# The completion script calls compdef; without a framework running compinit
# it is undefined, so load the completion system first.
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }

source <(herdr completion zsh)
