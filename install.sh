#!/bin/bash
# Bootstrap script for workbench dotfiles.
#
# Usage: bash install.sh [--all]
#
# What it does (core — always runs):
#   1. Installs the `task` runner if not present
#   2. Symlinks all bin/ scripts to ~/.local/bin/
#   3. Symlinks all zsh/*.zsh configs to ~/.config/zsh/config.d/
#   4. Symlinks git/.gitconfig to ~/.gitconfig
#   5. Symlinks Taskfile.yml and lib/ to ~/.config/task/
#   6. Adds ~/.local/bin to PATH in your shell rc file if needed
#
# Then presents a menu of optional components (brew, docker, iterm, ai).
# Each component is defined by a setup.sh + setup.conf in its directory.
# Components are run in the order listed in install.components.
#
# Flags:
#   --all   Skip the component selection menu and run all eligible components.
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DOTFILES_DIR/lib/ui.sh"

# ─── Flags ────────────────────────────────────────────────────────────────────

INSTALL_ALL=false
for _arg in "$@"; do [[ "$_arg" == "--all" ]] && INSTALL_ALL=true; done
unset _arg

# ─── Core helpers ─────────────────────────────────────────────────────────────

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
  # shellcheck disable=SC2016  # single quotes intentional — literal $HOME written to rc file
  if grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_rc" 2>/dev/null; then return; fi

  echo; info "Adding $HOME/.local/bin to PATH in $shell_rc"
  # shellcheck disable=SC2016  # single quotes intentional — literal $HOME written to rc file
  { echo ''; echo '# Add local bin to PATH'; echo 'export PATH="$HOME/.local/bin:$PATH"'; } >> "$shell_rc"
  echo -e "  ${GREEN}✓${NC} Updated $shell_rc"
}

# ─── Component system ─────────────────────────────────────────────────────────

# conf_get FILE KEY — reads a key = value line from FILE.
# Returns the trimmed value or empty string if the key is not found.
conf_get() {
  local file=$1 key=$2
  grep -m1 "^${key}[[:space:]]*=" "$file" 2>/dev/null \
    | sed "s/^${key}[[:space:]]*=[[:space:]]*//"
}

# platform_supported PLATFORMS — returns 0 if the current OS matches PLATFORMS.
# PLATFORMS is a space-separated list of: macos, linux. Empty or "all" means always supported.
platform_supported() {
  local platforms="${1:-all}"
  [[ "$platforms" == "all" || -z "$platforms" ]] && return 0
  if [[ "$OSTYPE" == "darwin"* ]]; then
    [[ "$platforms" == *"macos"* ]]
  else
    [[ "$platforms" == *"linux"* ]]
  fi
}

COMPONENT_DIRS=()
COMPONENT_LABELS=()
COMPONENT_DESCS=()
COMPONENT_PLATFORMS=()

# validate_components REGISTRY — cross-checks the registry against the filesystem.
# Exits with a hard error if:
#   - a registered component directory does not exist
#   - a registered component is missing setup.conf
#   - a directory has setup.conf but is not registered (orphan)
validate_components() {
  local registry=$1 errors=0 component dir conf

  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    if [[ ! -d "$DOTFILES_DIR/$component" ]]; then
      err "install.components: '$component' — directory not found"
      errors=$(( errors + 1 ))
    elif [[ ! -f "$DOTFILES_DIR/$component/setup.conf" ]]; then
      err "install.components: '$component' — missing setup.conf"
      errors=$(( errors + 1 ))
    fi
  done < "$registry"

  for conf in "$DOTFILES_DIR"/*/setup.conf; do
    [[ -f "$conf" ]] || continue
    dir=$(basename "$(dirname "$conf")")
    if ! grep -qx "$dir" "$registry" 2>/dev/null; then
      err "install.components: '$dir' has setup.conf but is not registered (orphan)"
      errors=$(( errors + 1 ))
    fi
  done

  if (( errors > 0 )); then
    err "$errors validation error(s) — fix install.components before continuing"
    exit 1
  fi
}

# discover_components REGISTRY — reads component metadata in registry order.
# Populates COMPONENT_DIRS, COMPONENT_LABELS, COMPONENT_DESCS, COMPONENT_PLATFORMS.
discover_components() {
  local registry=$1 component conf

  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    conf="$DOTFILES_DIR/$component/setup.conf"
    COMPONENT_DIRS+=("$component")
    COMPONENT_LABELS+=("$(conf_get "$conf" label)")
    COMPONENT_DESCS+=("$(conf_get "$conf" description)")
    COMPONENT_PLATFORMS+=("$(conf_get "$conf" platforms)")
  done < "$registry"
}

SELECTED_COMPONENTS=()

# select_components — presents a numbered menu and populates SELECTED_COMPONENTS.
# Platform-incompatible components are silently skipped.
# With --all flag or empty selection, all eligible components are selected.
select_components() {
  local eligible_dirs=() eligible_labels=() eligible_descs=()

  for i in "${!COMPONENT_DIRS[@]}"; do
    platform_supported "${COMPONENT_PLATFORMS[$i]}" || continue
    eligible_dirs+=("${COMPONENT_DIRS[$i]}")
    eligible_labels+=("${COMPONENT_LABELS[$i]}")
    eligible_descs+=("${COMPONENT_DESCS[$i]}")
  done

  if [[ ${#eligible_dirs[@]} -eq 0 ]]; then
    info "No components available for this platform"
    return
  fi

  if [[ "$INSTALL_ALL" == "true" ]]; then
    SELECTED_COMPONENTS=("${eligible_dirs[@]}")
    return
  fi

  echo
  info "Optional components:"
  for i in "${!eligible_dirs[@]}"; do
    printf "  [%d] %-22s ${DIM}%s${NC}\n" "$(( i + 1 ))" "${eligible_labels[$i]}" "${eligible_descs[$i]}"
  done
  echo
  read -rp "  Numbers to run (e.g. \"1 3\"), or Enter for all: " _selection
  echo

  if [[ -z "$_selection" ]]; then
    SELECTED_COMPONENTS=("${eligible_dirs[@]}")
    return
  fi

  local num
  for num in $_selection; do
    if (( num >= 1 && num <= ${#eligible_dirs[@]} )); then
      SELECTED_COMPONENTS+=("${eligible_dirs[$((num - 1))]}")
    else
      warn "Unknown option: $num — ignored"
    fi
  done
}

# run_components — executes setup.sh for each selected component in order.
run_components() {
  local component
  for component in "${SELECTED_COMPONENTS[@]}"; do
    echo
    bash "$DOTFILES_DIR/$component/setup.sh"
  done
}

# ─── Core installation ────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}\n"

command -v task >/dev/null 2>&1 || install_task
echo

mkdir -p ~/.local/bin
mkdir -p ~/.config/zsh/config.d

info "Installing scripts to ~/.local/bin/"
for _script in "$DOTFILES_DIR"/bin/*; do
  install_symlink "$_script" "$HOME/.local/bin/$(basename "$_script")"
done
unset _script

echo; info "Installing zsh configs to ~/.config/zsh/config.d/"
for _config in "$DOTFILES_DIR"/zsh/*.zsh; do
  install_symlink "$_config" "$HOME/.config/zsh/config.d/$(basename "$_config")"
done
unset _config

echo; info "Installing gitconfig"
install_symlink "$DOTFILES_DIR/git/.gitconfig" ~/.gitconfig

echo; info "ZSH configuration"
if [ ! -f "$HOME/.zshrc" ]; then
  cp "$DOTFILES_DIR/zsh/.zshrc" "$HOME/.zshrc"
  success "Copied .zshrc to $HOME/.zshrc"
  info "Add secrets and machine-specific config to $HOME/.env.local (sourced automatically, never committed)"
else
  info "$HOME/.zshrc already exists — skipping"
  echo -e "  ${DIM}Template: $DOTFILES_DIR/zsh/.zshrc${NC}"
fi

echo; info "Installing global Taskfile"
mkdir -p ~/.config/task
install_symlink "$DOTFILES_DIR/Taskfile.global.yml" ~/.config/task/Taskfile.yml
install_symlink "$DOTFILES_DIR/lib" ~/.config/task/lib

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# ─── Components ───────────────────────────────────────────────────────────────

REGISTRY="$DOTFILES_DIR/install.components"
validate_components "$REGISTRY"
discover_components  "$REGISTRY"
select_components
run_components

echo -e "\nReload your shell: ${BOLD}exec $(basename "$SHELL")${NC}"
