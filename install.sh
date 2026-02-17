#!/bin/bash

set -e

DOTFILES_DIR="$HOME/dotfiles"
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}\n"

# Create directories
mkdir -p ~/.local/bin
mkdir -p ~/.config/zsh/config.d

# Install scripts to PATH
echo -e "${GREEN}→${NC} Installing scripts to ~/.local/bin/"
for script in "$DOTFILES_DIR"/bin/*; do
  ln -sf "$script" ~/.local/bin/$(basename "$script")
  echo "  ✓ $(basename "$script")"
done

# Install zsh configs
echo -e "\n${GREEN}→${NC} Installing zsh configs to ~/.config/zsh/config.d/"
for config in "$DOTFILES_DIR"/zsh/*.zsh; do
  ln -sf "$config" ~/.config/zsh/config.d/$(basename "$config")
  echo "  ✓ $(basename "$config")"
done

# Install gitconfig
echo -e "\n${GREEN}→${NC} Installing gitconfig"
if [ -f ~/.gitconfig ]; then
  echo "  ⚠️  ~/.gitconfig exists, backing up to ~/.gitconfig.backup"
  cp ~/.gitconfig ~/.gitconfig.backup
fi
ln -sf "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig
echo "  ✓ .gitconfig"

# Ensure PATH includes ~/.local/bin
if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshrc 2>/dev/null; then
  echo -e "\n${GREEN}→${NC} Adding ~/.local/bin to PATH in ~/.zshrc"
  echo '' >> ~/.zshrc
  echo '# Add local bin to PATH' >> ~/.zshrc
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
  echo "  ✓ Updated ~/.zshrc"
fi

echo -e "\n${BOLD}${GREEN}✓ Installation complete!${NC}"
echo -e "\nReload your shell: ${BOLD}exec zsh${NC}"
