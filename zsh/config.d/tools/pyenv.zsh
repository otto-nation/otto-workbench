# pyenv — Python version manager
#
# Initialises pyenv shims and completions. No-op if pyenv is not installed.
# Requires Homebrew to be on PATH first (tools/homebrew.zsh loads before this).
#
# --no-rehash skips automatic shim rehash on every shell start, which is slow.
# Run `pyenv rehash` manually after installing a new Python version or a
# package that provides a binary (e.g. pip install black).
#
# If you use mise instead of pyenv, disable this snippet and enable tools/mise.zsh.
#
# Install:         brew install pyenv
# Docs:            https://github.com/pyenv/pyenv
# duplicate-check: pyenv init

command -v pyenv &>/dev/null || return 0

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

eval "$(pyenv init --path)"
eval "$(pyenv init - --no-rehash)"
