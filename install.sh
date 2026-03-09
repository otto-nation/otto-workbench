#!/bin/bash
# Bootstrap script for workbench dotfiles.
#
# Usage: bash install.sh
#
# What it does:
#   1. Installs the `task` runner if not present
#   2. Symlinks all bin/ scripts to ~/.local/bin/
#   3. Symlinks all zsh/*.zsh configs to ~/.config/zsh/config.d/
#   4. Symlinks git/.gitconfig to ~/.gitconfig
#   5. Symlinks Taskfile.yml and lib/ to ~/.config/task/
#   6. Adds ~/.local/bin to PATH in your shell rc file if needed
#   7. Runs docker/setup.sh to configure Colima socket and testcontainers
#   8. Runs ai/setup.sh to configure MCPs, agents, and guidelines (optional)
#   9. Opens the Taskfile AI command configuration in $EDITOR (optional)
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DOTFILES_DIR/lib/ui.sh"

# prompt_overwrite FILE — warns that FILE already exists and asks whether to overwrite it.
# Offers an optional backup step before overwriting. Returns 1 (skip) if the user declines.
prompt_overwrite() {
  local file=$1
  warn "$file already exists"
  printf "  Overwrite? [y/N] "
  read -n 1 -r REPLY
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then return 1; fi

  printf "  Create backup? [Y/n] "
  read -n 1 -r REPLY
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    cp "$file" "${file}.backup"
    echo -e "  ${GREEN}✓${NC} Backed up to ${file}.backup"
  fi
}

# install_task — prompts to install the go-task runner if it is not already present.
# Uses Homebrew on macOS, apt on Debian/Ubuntu, or prints a manual install URL otherwise.
install_task() {
  warn "Task (task runner) is not installed"
  printf "  Install it? [Y/n] "
  read -n 1 -r REPLY
  echo
  if [[ "$REPLY" =~ ^[Nn]$ ]]; then return; fi

  if [[ "$OSTYPE" == "darwin"* ]]; then
    info "Installing task via Homebrew..."
    brew install go-task/tap/go-task
  elif command -v apt-get >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1; then
      err "curl is required to install task. Install curl first: sudo apt-get install curl"
      return 1
    fi
    info "Installing task via apt..."
    sudo sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin
  else
    err "Unable to auto-install. See: https://taskfile.dev/installation/"
  fi
}

# update_path_in_shell_rc — appends ~/.local/bin to PATH in the user's shell rc file
# (~/.zshrc or ~/.bashrc) if the entry is not already present. No-op on unsupported shells.
update_path_in_shell_rc() {
  local shell_rc="" shell_name
  shell_name=$(basename "$SHELL")
  case "$shell_name" in
    zsh)  shell_rc="$HOME/.zshrc" ;;
    bash) shell_rc="$HOME/.bashrc" ;;
    *)    return ;;
  esac
  if grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_rc" 2>/dev/null; then return; fi

  echo; info "Adding $HOME/.local/bin to PATH in $shell_rc"
  echo '' >> "$shell_rc"
  echo '# Add local bin to PATH' >> "$shell_rc"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
  echo -e "  ${GREEN}✓${NC} Updated $shell_rc"
}

# configure_ai_command — runs `task --global ai:setup` to create ~/.config/task/taskfile.env, then
# optionally opens that file in $EDITOR so the user can set their AI_COMMAND preference.
configure_ai_command() {
  command -v task >/dev/null 2>&1 || return

  echo; info "Taskfile AI command"
  task --global ai:setup

  echo
  if confirm "  Configure your AI command now?"; then
    ${EDITOR:-nano} ~/.config/task/taskfile.env
    success "AI configuration updated"
  else
    warn "Remember to edit $HOME/.config/task/taskfile.env before using AI tasks"
  fi
}

# install_symlink SOURCE TARGET — creates or updates a symlink at TARGET pointing to SOURCE.
# Real files at TARGET trigger prompt_overwrite; existing symlinks are silently replaced
# (they were almost certainly left by a previous run of this script).
install_symlink() {
  local source=$1
  local target=$2
  local name
  name=$(basename "$source")

  # Only prompt if target is a real file — existing symlinks are silently updated since
  # they were almost certainly installed by a previous run of this script
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    prompt_overwrite "$target" || { echo -e "  ${DIM}⊘ Skipped $name${NC}"; return; }
  fi

  # -h prevents BSD ln from following an existing symlink at $target (macOS default behaviour
  # would dereference it, corrupting repo files or creating nested symlinks on re-runs)
  ln -sfh "$source" "$target"
  echo -e "  ${GREEN}✓${NC} $name"
}

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}\n"

command -v task >/dev/null 2>&1 || install_task
echo

# Create directories
mkdir -p ~/.local/bin
mkdir -p ~/.config/zsh/config.d

# Install scripts
info "Installing scripts to ~/.local/bin/"
for script in "$DOTFILES_DIR"/bin/*; do
  install_symlink "$script" "$HOME/.local/bin/$(basename "$script")"
done

# Install zsh configs
echo; info "Installing zsh configs to ~/.config/zsh/config.d/"
for config in "$DOTFILES_DIR"/zsh/*.zsh; do
  install_symlink "$config" "$HOME/.config/zsh/config.d/$(basename "$config")"
done

# Install gitconfig
echo; info "Installing gitconfig"
install_symlink "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig

# Docker / Colima setup
echo; bash "$DOTFILES_DIR/docker/setup.sh"

# Install .zshrc template
echo; info "ZSH configuration"
if [ ! -f "$HOME/.zshrc" ]; then
  cp "$DOTFILES_DIR/zsh/.zshrc" "$HOME/.zshrc"
  success "Copied .zshrc to $HOME/.zshrc"
  info "Add secrets and machine-specific config to $HOME/.env.local (sourced automatically, never committed)"
else
  info "$HOME/.zshrc already exists — skipping"
  echo -e "  ${DIM}Template: $DOTFILES_DIR/zsh/.zshrc${NC}"
fi

# Install global Taskfile and libs
echo; info "Installing global Taskfile"
mkdir -p ~/.config/task
install_symlink "$DOTFILES_DIR/Taskfile.global.yml" ~/.config/task/Taskfile.yml
install_symlink "$DOTFILES_DIR/lib" ~/.config/task/lib

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# Homebrew packages
bash "$DOTFILES_DIR/brew/setup.sh"

# iTerm2 setup (import color schemes, font instructions)
echo; info "iTerm2 setup"
if confirm "  Configure iTerm2 (import Gruvbox themes, show font instructions)?"; then bash "$DOTFILES_DIR/iterm/setup.sh"; fi

# AI Tools Setup (install agents before configuring which one to use)
echo; info "AI tools setup"
if confirm "  Configure AI tools (MCPs, agents, guidelines)?"; then bash "$DOTFILES_DIR/ai/setup.sh"; fi

# Taskfile AI command (configure after agents are installed)
configure_ai_command

echo -e "\nReload your shell: ${BOLD}exec $(basename "$SHELL")${NC}"
