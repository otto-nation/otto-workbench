#!/bin/bash
# Docker runtime setup.
#
# Usage: bash docker/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Prompts for your docker runtime (Colima, OrbStack, etc.)
#   2. Runs runtime-specific socket/daemon setup
#   3. Symlinks testcontainers.properties so Gradle tests work in non-interactive shells
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

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

# install_symlink SOURCE TARGET — creates or updates a symlink at TARGET pointing to SOURCE.
# Real files at TARGET trigger prompt_overwrite; existing symlinks are silently replaced.
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

# ─── Runtime selection ────────────────────────────────────────────────────────

select_runtime() {
  local runtimes=()
  local dir

  # Discover runtimes dynamically — any subdirectory containing setup.sh qualifies
  for dir in "$SCRIPT_DIR"/*/; do
    [[ -f "${dir}setup.sh" ]] && runtimes+=("$(basename "$dir")")
  done

  if [[ ${#runtimes[@]} -eq 0 ]]; then
    err "No runtimes found in $SCRIPT_DIR"
    exit 1
  fi

  echo "Which docker runtime are you using?"
  local i=1
  for runtime in "${runtimes[@]}"; do
    echo "  [$i] $runtime"
    i=$(( i + 1 ))
  done
  echo
  read -rp "Enter number: " selection
  echo

  if [[ ! "$selection" =~ ^[0-9]+$ ]] || (( selection < 1 || selection > ${#runtimes[@]} )); then
    err "Invalid selection: $selection"
    exit 1
  fi

  DOCKER_RUNTIME="${runtimes[$((selection - 1))]}"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Docker setup${NC}\n"

select_runtime

info "Runtime: $DOCKER_RUNTIME"
# Source the runtime-specific setup so it shares helpers defined above
. "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh"

# Testcontainers reads ~/.testcontainers.properties before checking DOCKER_HOST, so Gradle
# test runs in non-interactive shells (where aliases-docker.zsh is not sourced) also work.
echo
info "Testcontainers"
install_symlink "$SCRIPT_DIR/testcontainers.properties" ~/.testcontainers.properties

echo
success "Docker setup complete!"
