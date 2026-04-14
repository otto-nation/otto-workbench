# mise — polyglot dev tool version manager
#
# Activates mise shims and hooks for all managed runtimes (node, python, java, go, etc.)
# No-op if mise is not installed.
#
# mise replaces nvm, jenv, pyenv, and asdf. If you still have those tools installed
# and active, disable their snippets to avoid PATH conflicts.
#
# Install:         curl -fsSL https://mise.run | sh
# Docs:            https://mise.jdx.dev
# duplicate-check: mise activate

command -v mise &>/dev/null || return 0

eval "$(mise activate zsh)"
