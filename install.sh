#!/bin/bash

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

prompt_overwrite() {
  local file=$1
  echo -e "${YELLOW}⚠️  $file already exists${NC}"
  read -p "Overwrite? [y/N] " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "Create backup? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      cp "$file" "${file}.backup"
      echo "  ✓ Backed up to ${file}.backup"
    fi
    return 0
  fi
  return 1
}

install_symlink() {
  local source=$1
  local target=$2
  local name=$(basename "$source")
  
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    if prompt_overwrite "$target"; then
      ln -sf "$source" "$target"
      echo "  ✓ $name"
    else
      echo "  ⊘ Skipped $name"
    fi
  else
    ln -sf "$source" "$target"
    echo "  ✓ $name"
  fi
}

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}\n"

# Check if task is installed
if ! command -v task >/dev/null 2>&1; then
  echo -e "${YELLOW}⚠️  Task (task runner) is not installed${NC}"
  read -p "Would you like to install it? [Y/n] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
      echo "Installing task via Homebrew..."
      brew install go-task/tap/go-task
    elif command -v apt-get >/dev/null 2>&1; then
      echo "Installing task via apt..."
      sudo sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin
    else
      echo -e "${RED}Unable to auto-install. Please install manually: https://taskfile.dev/installation/${NC}"
    fi
  fi
fi
echo ""

# Create directories
mkdir -p ~/.local/bin
mkdir -p ~/.config/zsh/config.d

# Install scripts
echo -e "${GREEN}→${NC} Installing scripts to ~/.local/bin/"
for script in "$DOTFILES_DIR"/bin/*; do
  install_symlink "$script" ~/.local/bin/$(basename "$script")
done

# Install zsh configs
echo -e "\n${GREEN}→${NC} Installing zsh configs to ~/.config/zsh/config.d/"
for config in "$DOTFILES_DIR"/zsh/*.zsh; do
  install_symlink "$config" ~/.config/zsh/config.d/$(basename "$config")
done

# Install gitconfig
echo -e "\n${GREEN}→${NC} Installing gitconfig"
install_symlink "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig

# Install global Taskfile
echo -e "\n${GREEN}→${NC} Installing global Taskfile"
install_symlink "$DOTFILES_DIR/Taskfile.yml" ~/Taskfile.yml

# Add ~/.local/bin to PATH
SHELL_RC=""
if [ -n "$ZSH_VERSION" ]; then
  SHELL_RC=~/.zshrc
elif [ -n "$BASH_VERSION" ]; then
  SHELL_RC=~/.bashrc
fi

if [ -n "$SHELL_RC" ] && ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$SHELL_RC" 2>/dev/null; then
  echo -e "\n${GREEN}→${NC} Adding ~/.local/bin to PATH in $SHELL_RC"
  echo '' >> "$SHELL_RC"
  echo '# Add local bin to PATH' >> "$SHELL_RC"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
  echo "  ✓ Updated $SHELL_RC"
fi

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# AI Tools Setup (install agents before configuring which one to use)
echo -e "\n${GREEN}→${NC} AI tools setup"
read -p "Configure AI tools (MCPs, agents, guidelines)? [Y/n] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
  bash "$DOTFILES_DIR/ai/setup.sh"
fi

# Taskfile AI command (configure after agents are installed)
if command -v task >/dev/null 2>&1; then
  echo -e "\n${GREEN}→${NC} Taskfile AI command"
  task setup-ai

  echo ""
  read -p "Configure your AI command now? [Y/n] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    ${EDITOR:-nano} ~/.config/task/taskfile.env
    echo -e "${GREEN}✓${NC} AI configuration updated"
  else
    echo -e "${YELLOW}⚠️${NC}  Remember to edit ~/.config/task/taskfile.env before using AI tasks"
  fi
fi

echo -e "\nReload your shell: ${BOLD}exec $(basename $SHELL)${NC}"
