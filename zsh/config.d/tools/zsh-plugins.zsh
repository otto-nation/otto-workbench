# Brew-installed zsh plugins (zsh-syntax-highlighting, zsh-history-substring-search, etc.)
#
# Auto-discovers and sources all zsh-* plugins from Homebrew's share directory.
# To add a new plugin, just add it to brew/shell/shell.Brewfile — no config changes needed.
#
# duplicate-check: share/zsh-.*/zsh-.*\.zsh

# BREW_PREFIX is exported by homebrew.zsh only when brew is installed; on a
# machine without it (e.g. a Linux host) there is nothing to source. (N) keeps
# an empty share/ from tripping zsh's nomatch error; - follows brew's symlinks.
if [[ -n "${BREW_PREFIX:-}" ]]; then
  for plugin in "$BREW_PREFIX"/share/zsh-*/zsh-*.zsh(-.N); do
    source "$plugin"
  done
fi

# history-substring-search: up/down arrows filter history by what you've already typed
if (( $+functions[history-substring-search-up] )); then
  bindkey '^[[A' history-substring-search-up
  bindkey '^[[B' history-substring-search-down
fi
