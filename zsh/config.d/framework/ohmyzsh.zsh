# oh-my-zsh shell framework
#
# Loaded in framework/ (first) so its completion system and plugin hooks
# are available to everything that loads after it — particularly aliases/,
# which may define shortcuts for completions oh-my-zsh registers.
#
# ZSH_THEME is intentionally empty: starship (prompt/) manages the prompt.
# If you switch prompt managers, keep ZSH_THEME="" to avoid conflicts.
#
# To add/remove plugins, edit this file in the workbench repo and commit.
# Machine-specific plugin overrides can go in ~/.zshrc after the loader source.
#
# Install: https://ohmyz.sh/
# Docs:    https://github.com/ohmyzsh/ohmyzsh/wiki

[[ -d "$HOME/.oh-my-zsh" ]] || return 0

export ZSH="$HOME/.oh-my-zsh"

# Empty theme — starship (prompt/starship.zsh) manages the prompt.
# Setting a theme here would conflict with starship's PROMPT hook.
ZSH_THEME=""

plugins=(
  git     # git aliases and tab completions
  dotenv  # auto-loads .env files when you cd into a project directory
  macos   # macOS-specific aliases (e.g. `ofd` to open Finder here)
)

source "$ZSH/oh-my-zsh.sh"
