#!/bin/bash

set -e

DOTFILES_DIR="$HOME/dotfiles"
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
  [[ $REPLY =~ ^[Yy]$ ]]
}

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}\n"

# Create directories
mkdir -p ~/.local/bin
mkdir -p ~/.config/zsh/config.d

# Install scripts to PATH
echo -e "${GREEN}→${NC} Installing scripts to ~/.local/bin/"
for script in "$DOTFILES_DIR"/bin/*; do
  target=~/.local/bin/$(basename "$script")
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    if prompt_overwrite "$target"; then
      ln -sf "$script" "$target"
      echo "  ✓ $(basename "$script")"
    else
      echo "  ⊘ Skipped $(basename "$script")"
    fi
  else
    ln -sf "$script" "$target"
    echo "  ✓ $(basename "$script")"
  fi
done

# Install zsh configs
echo -e "\n${GREEN}→${NC} Installing zsh configs to ~/.config/zsh/config.d/"
for config in "$DOTFILES_DIR"/zsh/*.zsh; do
  target=~/.config/zsh/config.d/$(basename "$config")
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    if prompt_overwrite "$target"; then
      ln -sf "$config" "$target"
      echo "  ✓ $(basename "$config")"
    else
      echo "  ⊘ Skipped $(basename "$config")"
    fi
  else
    ln -sf "$config" "$target"
    echo "  ✓ $(basename "$config")"
  fi
done

# Install gitconfig
echo -e "\n${GREEN}→${NC} Installing gitconfig"
if [ -e ~/.gitconfig ] && [ ! -L ~/.gitconfig ]; then
  if prompt_overwrite ~/.gitconfig; then
    ln -sf "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig
    echo "  ✓ .gitconfig"
  else
    echo "  ⊘ Skipped .gitconfig"
  fi
else
  ln -sf "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig
  echo "  ✓ .gitconfig"
fi

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
