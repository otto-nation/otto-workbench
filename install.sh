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
#   7. Runs ai/setup.sh to configure MCPs, agents, and guidelines (optional)
#   8. Opens the Taskfile AI command configuration in $EDITOR (optional)
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DOTFILES_DIR/lib/ui.sh"

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
    info "Installing task via apt..."
    sudo sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin
  else
    err "Unable to auto-install. See: https://taskfile.dev/installation/"
  fi
}

update_path_in_shell_rc() {
  local shell_rc=""
  if [ -n "$ZSH_VERSION" ]; then shell_rc=~/.zshrc; fi
  if [ -n "$BASH_VERSION" ]; then shell_rc=~/.bashrc; fi
  if [ -z "$shell_rc" ]; then return; fi
  if grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_rc" 2>/dev/null; then return; fi

  echo; info "Adding ~/.local/bin to PATH in $shell_rc"
  echo '' >> "$shell_rc"
  echo '# Add local bin to PATH' >> "$shell_rc"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
  echo -e "  ${GREEN}✓${NC} Updated $shell_rc"
}

configure_ai_command() {
  command -v task >/dev/null 2>&1 || return

  echo; info "Taskfile AI command"
  task setup-ai

  echo
  printf "  Configure your AI command now? [Y/n] "
  read -n 1 -r REPLY
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    ${EDITOR:-nano} ~/.config/task/taskfile.env
    success "AI configuration updated"
  else
    warn "Remember to edit ~/.config/task/taskfile.env before using AI tasks"
  fi
}

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

  ln -sf "$source" "$target"
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

# Install global Taskfile and libs
echo; info "Installing global Taskfile"
mkdir -p ~/.config/task
install_symlink "$DOTFILES_DIR/Taskfile.yml" ~/.config/task/Taskfile.yml
install_symlink "$DOTFILES_DIR/lib" ~/.config/task/lib

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# AI Tools Setup (install agents before configuring which one to use)
echo; info "AI tools setup"
printf "  Configure AI tools (MCPs, agents, guidelines)? [Y/n] "
read -n 1 -r REPLY
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then bash "$DOTFILES_DIR/ai/setup.sh"; fi

# Taskfile AI command (configure after agents are installed)
configure_ai_command

echo -e "\nReload your shell: ${BOLD}exec $(basename "$SHELL")${NC}"
