# loader.zsh — workbench shell configuration loader
#
# Copied (never symlinked) to ~/.config/zsh/config.d/ by install.sh so it
# survives if the workbench repo is moved or deleted.
#
# WHAT IT DOES:
#   Sources each config.d layer in the correct order. Within each layer,
#   files are sourced alphabetically. Missing directories are silently skipped.
#   Broken symlinks are skipped automatically by the *.zsh(.N) glob.
#
# LOAD ORDER (matters — do not reorder without reason):
#   framework/  shell framework init (oh-my-zsh, zinit…)     — must be first
#   tools/      runtime version managers (pyenv, nvm, mise…) — needs framework PATH
#   aliases/    command shortcuts                             — always deployed
#   prompt/     prompt init (starship)                       — must be last
#
# ADDING A NEW SNIPPET:
#   Drop a .zsh file into the correct layer directory in the workbench repo.
#   Re-run install.sh to symlink it. No changes needed here.
#
# ADDING A NEW LAYER:
#   1. Create the directory in zsh/config.d/ in the workbench repo
#   2. Add a _wb_load call below at the correct position
#   3. Re-run install.sh — it will copy this updated loader and create the directory
#
#   Layer order is intentionally explicit here (not auto-discovered) because load
#   order is semantically significant: framework must initialise PATH before tools
#   can add version-manager shims, and aliases must resolve before prompt can
#   reference them. Auto-discovery by filename or mtime would silently break this
#   contract whenever a new layer is added.
#
# DISABLING A SNIPPET:
#   Remove its symlink from ~/.config/zsh/config.d/<layer>/
#   The workbench will recreate it on the next install.sh run unless you
#   remove the source file from the workbench repo.
#
# This file is managed by the workbench. Re-running install.sh updates it.
# Machine-specific config belongs in ~/.zshrc (after the source line) or ~/.env.local.
#
# duplicate-check:       config\.d/\*\.zsh
# duplicate-check-label: config.d glob

_wb_load() {
  local dir="$HOME/.config/zsh/config.d/$1"
  [[ -d "$dir" ]] || return 0
  local f
  for f in "$dir"/*.zsh(.N); do
    source "$f"
  done
}

# The tool directories and ~/.env.local, from env.sh — the one definition every
# non-interactive caller (sync, maintenance, herdr's remote commands) reads too.
#
# Before any layer: the tools layer gates each snippet on its command being
# found (mise.zsh returns early without mise), and ~/.local/bin is where the
# mise and Pi installers put their binaries — a PATH line in ~/.zshrc after the
# source line comes too late. ~/.env.local is sourced after the PATH so a
# machine-specific PATH there still wins, and before the layers so every layer
# can read it. See zsh/.env.local.template for what belongs there vs
# ~/.config/task/taskfile.env.
if [[ -f "$HOME/.config/zsh/env.sh" ]]; then
  source "$HOME/.config/zsh/env.sh"
  workbench_env
else
  # No fallback copy of env.sh's PATH and ~/.env.local handling here: a second
  # copy is what drifted before. ~/.local/bin is not on PATH in this state, so
  # the fix is named by its absolute path.
  echo "⚠  ~/.config/zsh/env.sh is missing — run ~/.local/bin/otto-workbench sync to restore PATH and ~/.env.local" >&2
fi

_wb_load framework
_wb_load tools
_wb_load aliases
_wb_load prompt

unfunction _wb_load
