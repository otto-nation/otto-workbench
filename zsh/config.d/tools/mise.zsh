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
# requires-cmd:    mise

# Resolve the path and verify it exists — command -v may return a stale hash
# entry pointing to a binary that was removed (e.g. after switching from brew
# to direct install). The -x check catches that case and bails early.
_mise_bin="$(command -v mise 2>/dev/null)"
[[ -x "$_mise_bin" ]] || return 0

# Suppress stderr during activation to avoid "Current directory does not exist"
# warning when exec zsh is run from a deleted directory (the activation script
# calls _mise_hook immediately, which fails when CWD is gone)
eval "$("$_mise_bin" activate zsh)" 2>/dev/null

# Override the precmd/chpwd hooks to guard all future invocations
function _mise_hook_precmd() {
  [[ -d . ]] || return 0
  eval "$("$_mise_bin" hook-env -s zsh --reason precmd)"
}
function _mise_hook_chpwd() {
  [[ -d . ]] || return 0
  eval "$("$_mise_bin" hook-env -s zsh --reason chpwd)"
}
