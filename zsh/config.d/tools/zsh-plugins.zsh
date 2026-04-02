# Brew-installed zsh plugins (zsh-syntax-highlighting, zsh-history-substring-search, etc.)
#
# Auto-discovers and sources all zsh-* plugins from Homebrew's share directory.
# To add a new plugin, just add it to brew/shell/shell.Brewfile — no config changes needed.
#
# duplicate-check: share/zsh-.*/zsh-.*\.zsh

for plugin in /opt/homebrew/share/zsh-*/zsh-*.zsh; do
  [[ -f "$plugin" ]] && source "$plugin"
done

# history-substring-search: up/down arrows filter history by what you've already typed
if (( $+functions[history-substring-search-up] )); then
  bindkey '^[[A' history-substring-search-up
  bindkey '^[[B' history-substring-search-down
fi
