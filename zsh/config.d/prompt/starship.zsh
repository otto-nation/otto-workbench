# Starship — cross-shell prompt
#
# Loaded last (prompt/) so its PROMPT hook overrides anything set by the shell
# framework (oh-my-zsh). Loading before the framework would result in
# oh-my-zsh's theme system overwriting starship's hook.
#
# Config: ~/.config/starship.toml — symlinked from workbench/zsh/starship.toml
#         by install.sh. Edit starship.toml in the workbench repo and commit.
#
# No-op if starship is not installed.
#
# Install: brew install starship
# Docs:    https://starship.rs

command -v starship &>/dev/null || return 0

eval "$(starship init zsh)"
