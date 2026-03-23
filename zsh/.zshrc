# ~/.zshrc — your interactive shell configuration
#
# Created once by the workbench installer. This file is yours to edit —
# the workbench never modifies it after the initial bootstrap.
#
# ─── HOW THE WORKBENCH INTEGRATES ────────────────────────────────────────────
#
# The source line below loads loader.zsh, which sources each config layer
# in the correct order:
#
#   framework/  shell framework (oh-my-zsh)           — loaded first
#   tools/      version managers (pyenv, nvm, sdkman) — loaded second
#   aliases/    command shortcuts                      — always deployed
#   prompt/     starship prompt                        — loaded last
#
# Each layer is a directory under ~/.config/zsh/config.d/.
# Snippets are symlinked there by install.sh — run it to add or update them.
#
# To see what's active on this machine:
#   ls ~/.config/zsh/config.d/
#
# To disable a snippet: remove its symlink from the relevant layer directory.
# To add machine-specific config: add it below the integration block, or use
#   ~/.env.local (sourced automatically, never committed).
#
# ─── WORKBENCH INTEGRATION — do not remove ───────────────────────────────────

if [[ -f "$HOME/.config/zsh/config.d/loader.zsh" ]]; then
  source "$HOME/.config/zsh/config.d/loader.zsh"
else
  echo "⚠  workbench not connected — run install.sh to restore" >&2
fi

# ─── machine-specific config ──────────────────────────────────────────────────
# Add your own setup below: tool paths, extra aliases, shell options, etc.
# For secrets and per-machine environment variables, prefer ~/.env.local.
