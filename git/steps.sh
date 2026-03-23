#!/bin/bash
# Git configuration setup.
#
# Usage: bash git/steps.sh
#        (also sourced by install.sh and bin/otto-workbench for step functions)
#
# What it does:
#   1. Sets up ~/.gitconfig to include the workbench shared config
#   2. Bootstraps ~/.gitconfig.local from template if absent (identity, GPG, credentials)
#   3. Installs a global pre-commit hook for gitleaks — protects every repo on this machine
#
# ~/.gitconfig is a real file (not a symlink) so `git config --global` never touches the
# tracked repo file. Machine-specific values live only in ~/.gitconfig.local.
#
# Re-running is safe — all steps are idempotent.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  DOTFILES_DIR="$(cd "$_SETUP_DIR/.." && pwd)"
  . "$DOTFILES_DIR/lib/ui.sh"
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_gitconfig — sets up ~/.gitconfig with [include] lines for the shared
# workbench config and ~/.gitconfig.local. Bootstraps the local file from
# template on a new machine.
step_gitconfig() {
  local repo="${DOTFILES_DIR:-"$WORKBENCH_DIR"}"
  local shared="$repo/git/.gitconfig"
  local template="$repo/git/.gitconfig.local.template"

  if [[ ! -f "$GITCONFIG_FILE" ]]; then
    cat > "$GITCONFIG_FILE" <<EOF
# ~/.gitconfig — global git config for this machine.
#
# This file is NOT tracked by any repo and intentionally contains no settings directly.
#
# Architecture:
#   git/.gitconfig     → shared aliases, colors, and behavior (version-controlled in workbench)
#   ~/.gitconfig.local → machine-specific identity, GPG, and credentials (never committed)
#
# To change shared settings: edit git/.gitconfig in the workbench repo and commit.
# To change machine-specific settings: edit ~/.gitconfig.local.
# If you move the workbench repo, re-run install.sh to update the path below.

[include]
	path = $shared

[include]
	path = $GITCONFIG_LOCAL_FILE
EOF
    success "Created $GITCONFIG_FILE"
  else
    if ! grep -qF "path = $shared" "$GITCONFIG_FILE"; then
      printf '\n[include]\n\tpath = %s\n' "$shared" >> "$GITCONFIG_FILE"
      success "Added shared gitconfig include"
    else
      success "gitconfig include up to date"
    fi
    if ! grep -qF "path = $GITCONFIG_LOCAL_FILE" "$GITCONFIG_FILE"; then
      printf '\n[include]\n\tpath = %s\n' "$GITCONFIG_LOCAL_FILE" >> "$GITCONFIG_FILE"
      success "Added local gitconfig include"
    fi
  fi

  if [[ ! -f "$GITCONFIG_LOCAL_FILE" ]]; then
    cp "$template" "$GITCONFIG_LOCAL_FILE"
    warn "Created $GITCONFIG_LOCAL_FILE from template — edit it to set your identity and credential helpers"
  else
    success ".gitconfig.local already exists"
  fi
}

# step_global_hooks — symlinks the workbench pre-commit hook into $GIT_HOOKS_DIR
# and sets git's global core.hooksPath so every repo on this machine is protected.
step_global_hooks() {
  local repo="${DOTFILES_DIR:-"$WORKBENCH_DIR"}"
  mkdir -p "$GIT_HOOKS_DIR"
  install_symlink "$repo/git/hooks/pre-commit" "$GIT_HOOKS_DIR/pre-commit"
  install_symlink "$repo/git/hooks/pre-push-global" "$GIT_HOOKS_DIR/pre-push"
  git config --global core.hooksPath "$GIT_HOOKS_DIR"
  success "global core.hooksPath → $GIT_HOOKS_DIR"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Git setup${NC}\n"

  echo; info "git config → $GITCONFIG_FILE"
  step_gitconfig

  echo; info "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks

  echo
  success "Git setup complete!"
fi
