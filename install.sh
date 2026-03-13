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
export DOTFILES_DIR
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
    zsh)  shell_rc="$ZSHRC_FILE" ;;
    bash) shell_rc="$BASHRC_FILE" ;;
    *)    return ;;
  esac
  # shellcheck disable=SC2016  # single quotes intentional — literal $HOME written to rc file
  if grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_rc" 2>/dev/null; then return; fi

  echo; info "Adding $LOCAL_BIN_DIR to PATH in $shell_rc"
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

  local _sel
  select_menu _sel "${#eligible_dirs[@]}" --default all
  [[ -z "$_sel" ]] && return

  local num
  for num in $_sel; do
    SELECTED_COMPONENTS+=("${eligible_dirs[$((num - 1))]}")
  done
}

# run_components — executes setup.sh for each selected component.
# If setup.conf defines a `check` command, runs it first; skips the component if it exits 0.
# DOTFILES_DIR is exported so check commands can reference it.
run_components() {
  [[ ${#SELECTED_COMPONENTS[@]} -eq 0 ]] && return

  local total=${#SELECTED_COMPONENTS[@]} index=1 component label check_cmd

  for component in "${SELECTED_COMPONENTS[@]}"; do
    label=$(conf_get "$DOTFILES_DIR/$component/setup.conf" label)
    check_cmd=$(conf_get "$DOTFILES_DIR/$component/setup.conf" check)
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}${label:-$component}${NC}"

    # shellcheck disable=SC2086
    if [[ -n "$check_cmd" ]] && bash -c "$check_cmd" > /dev/null 2>&1; then
      success "already configured"
    else
      bash "$DOTFILES_DIR/$component/setup.sh"
    fi

    index=$(( index + 1 ))
  done
}

# print_install_summary — prints a structured summary after all components run.
# Sources each selected component's summary.sh (if present) and calls its
# print_<component>_summary() function, following the same discovery pattern
# used by ai/setup.sh for per-tool summaries.
print_install_summary() {
  local shell_name readme
  shell_name=$(basename "$SHELL")
  readme="$DOTFILES_DIR/README.md"

  echo
  echo -e "${BOLD}${GREEN}━━━ All done ━━━${NC}"
  echo

  echo -e "  ${CYAN}Installed${NC}"
  echo -e "  ${DIM}  • bin scripts      → $LOCAL_BIN_DIR/${NC}"
  echo -e "  ${DIM}  • zsh configs      → $ZSH_CONFIG_DIR/${NC}"
  echo -e "  ${DIM}  • gitconfig        → $GITCONFIG_FILE (includes git/.gitconfig + ~/.gitconfig.local)${NC}"
  echo -e "  ${DIM}  • global Taskfile  → $TASK_CONFIG_DIR/${NC}"
  local component
  for component in "${SELECTED_COMPONENTS[@]}"; do
    local label
    label=$(conf_get "$DOTFILES_DIR/$component/setup.conf" label)
    echo -e "  ${DIM}  • ${label:-$component}${NC}"
  done

  echo
  echo -e "  ${CYAN}Next steps${NC}"
  echo -e "  ${DIM}  1. Reload your shell:${NC}  ${BOLD}exec $shell_name${NC}"

  for component in "${SELECTED_COMPONENTS[@]}"; do
    local summary_file="$DOTFILES_DIR/$component/summary.sh"
    # shellcheck source=/dev/null
    [[ -f "$summary_file" ]] && . "$summary_file"
    declare -f "print_${component}_summary" > /dev/null && "print_${component}_summary"
  done

  echo
  echo -e "  ${DIM}Day-to-day reference:  $readme${NC}"
  echo
}

# ─── Core installation ────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}"
echo -e "  ${DIM}✓${NC} installed  ${DIM}✓ up to date  ⊘ skipped  ⚠ attention needed${NC}"
echo

command -v task >/dev/null 2>&1 || install_task
echo

mkdir -p "$LOCAL_BIN_DIR"
mkdir -p "$ZSH_CONFIG_DIR"

# _step_gitconfig DOTFILES_DIR — sets up ~/.gitconfig to include the workbench
# shared config. Unlike other dotfiles, ~/.gitconfig is a real file (not a symlink)
# so that `git config --global` writes safely without touching the tracked repo file.
#
# On a new machine:
#   1. Creates ~/.gitconfig with [include] pointing to the workbench gitconfig
#   2. Creates ~/.gitconfig.local from the template if absent (machine-specific values)
#
# On an existing machine:
#   Ensures the [include] line is present (idempotent).
_step_gitconfig() {
  local dotfiles="$1"
  local shared="$dotfiles/git/.gitconfig"
  local template="$dotfiles/git/.gitconfig.local.template"
  local target="$GITCONFIG_FILE"
  local local_config="$HOME/.gitconfig.local"
  local include_line="path = $shared"
  local local_include_line="path = $local_config"

  # Ensure ~/.gitconfig exists and contains the shared workbench include
  if [[ ! -f "$target" ]]; then
    printf '[include]\n\t%s\n\n[include]\n\t%s\n' "$include_line" "$local_include_line" > "$target"
    success "Created $target with workbench include"
  else
    if ! grep -qF "$include_line" "$target"; then
      printf '\n[include]\n\t%s\n' "$include_line" >> "$target"
      success "Added workbench include to $target"
    else
      success "gitconfig include already present"
    fi
    if ! grep -qF "$local_include_line" "$target"; then
      printf '\n[include]\n\t%s\n' "$local_include_line" >> "$target"
      success "Added local include to $target"
    fi
  fi

  # Bootstrap ~/.gitconfig.local from template if absent
  if [[ ! -f "$local_config" ]]; then
    cp "$template" "$local_config"
    warn "Created $local_config from template — edit it to set your identity and credential helpers"
  else
    success ".gitconfig.local already exists"
  fi
}

# _step_global_hooks DOTFILES_DIR — installs a global git pre-commit hook for
# secret scanning with gitleaks. Applies to every git repo on this machine.
_step_global_hooks() {
  local src="$1/hooks" dst="$HOME/.git-hooks"
  mkdir -p "$dst"
  install_symlink "$src/pre-commit" "$dst/pre-commit"
  git config --global core.hooksPath "$dst"
  success "global core.hooksPath → $dst"
}

# _step_zshrc — copies the workbench .zshrc template if absent; if it differs,
# shows a compact diff and offers update / keep / view-full choices.
_step_zshrc() {
  local template="$DOTFILES_DIR/zsh/.zshrc"
  if [ ! -f "$ZSHRC_FILE" ]; then
    cp "$template" "$ZSHRC_FILE"
    success "Copied .zshrc"
    info "Add secrets and machine-specific config to $ENV_LOCAL_FILE (sourced automatically, never committed)"
  elif diff -q "$template" "$ZSHRC_FILE" > /dev/null 2>&1; then
    success ".zshrc matches workbench template — up to date"
  else
    warn ".zshrc differs from workbench template"
    echo
    diff -u "$template" "$ZSHRC_FILE" | tail -n +3 | head -30
    local diff_lines
    diff_lines=$(diff -u "$template" "$ZSHRC_FILE" | tail -n +3 | wc -l | tr -d ' ')
    if [[ "$diff_lines" -gt 30 ]]; then echo -e "  ${DIM}... $diff_lines diff lines total${NC}"; fi
    echo
    echo -e "  ${DIM}Machine-specific config belongs in $ENV_LOCAL_FILE (never committed)${NC}"
    echo
    local _choice
    read -rp "  [u]pdate from template / [k]eep mine / [v]iew full diff [k]: " _choice
    case "${_choice:-k}" in
      u|U)
        cp "$ZSHRC_FILE" "${ZSHRC_FILE}.backup"
        echo -e "  ${GREEN}✓${NC} Backed up to ${ZSHRC_FILE}.backup"
        cp "$template" "$ZSHRC_FILE"
        success "Updated .zshrc from workbench template"
        ;;
      v|V)
        diff -u "$template" "$ZSHRC_FILE" | "${PAGER:-less}"
        echo
        read -rp "  [u]pdate from template / [k]eep mine [k]: " _choice
        if [[ "${_choice:-k}" =~ ^[Uu]$ ]]; then
          cp "$ZSHRC_FILE" "${ZSHRC_FILE}.backup"
          echo -e "  ${GREEN}✓${NC} Backed up to ${ZSHRC_FILE}.backup"
          cp "$template" "$ZSHRC_FILE"
          success "Updated .zshrc from workbench template"
        else
          info "Keeping existing .zshrc"
        fi
        ;;
      *)
        info "Keeping existing .zshrc"
        ;;
    esac
  fi
}

echo; info "bin scripts → $LOCAL_BIN_DIR/"
symlink_dir "$DOTFILES_DIR/bin" "$LOCAL_BIN_DIR"

echo; info "zsh configs → $ZSH_CONFIG_DIR/"
symlink_dir "$DOTFILES_DIR/zsh" "$ZSH_CONFIG_DIR" "*.zsh"

echo; info "git config → $GITCONFIG_FILE"
_step_gitconfig "$DOTFILES_DIR"

echo; info "global git hooks → ~/.git-hooks/"
_step_global_hooks "$DOTFILES_DIR"

echo; info "starship → $STARSHIP_CONFIG_FILE"
install_symlink "$DOTFILES_DIR/zsh/starship.toml" "$STARSHIP_CONFIG_FILE"

echo; info "ZSH configuration (.zshrc)"
_step_zshrc

echo; info "global Taskfile → $TASK_CONFIG_DIR/"
mkdir -p "$TASK_CONFIG_DIR"
install_symlink "$DOTFILES_DIR/Taskfile.global.yml" "$TASK_CONFIG_DIR/Taskfile.yml"
install_symlink "$DOTFILES_DIR/lib" "$TASK_CONFIG_DIR/lib"

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# ─── Components ───────────────────────────────────────────────────────────────

REGISTRY="$DOTFILES_DIR/install.components"
validate_components "$REGISTRY"
discover_components  "$REGISTRY"
select_components
run_components

print_install_summary
