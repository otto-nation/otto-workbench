#!/bin/bash
# Git configuration setup.
#
# Usage: bash git/steps.sh
#        (also sourced by install.sh and bin/otto-workbench for step functions)
#
# What it does:
#   1. Bootstraps ~/.gitconfig from template on a new machine (identity, GPG, credentials)
#   2. Ensures ~/.gitconfig includes the shared workbench config (git/gitconfig.shared)
#   3. Installs global git hooks for gitleaks — protects every repo on this machine
#
# Architecture (2-layer):
#   ~/.gitconfig         → your machine: identity, GPG, overrides (+ includes shared config)
#   git/gitconfig.shared → shared aliases, colors, and behavior (version-controlled)
#
# git config --global writes directly to ~/.gitconfig — this is expected and fine.
# Re-running is safe — all steps are idempotent.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# _gitconfig_bootstrap — copies the template into ~/.gitconfig when the file
# does not yet exist (new machine). The template includes placeholder values
# for identity and GPG that the user fills in.
_gitconfig_bootstrap() {
  if [[ ! -f "$GITCONFIG_FILE" ]]; then
    cp "$GIT_CONFIG_TEMPLATE" "$GITCONFIG_FILE"
    warn "Created $GITCONFIG_FILE from template — edit it to set your identity and GPG key"
  fi
}

# _gitconfig_ensure_include PATH — appends an [include] stanza for PATH if not
# already present. Silent no-op when the include already exists.
_gitconfig_ensure_include() {
  local include_path="$1"
  if ! grep -qF "path = $include_path" "$GITCONFIG_FILE"; then
    printf '\n[include]\n\tpath = %s\n' "$include_path" >> "$GITCONFIG_FILE"
    success "Added include: $(basename "$include_path")"
  fi
}

# step_gitconfig — ensures ~/.gitconfig exists and includes the shared
# workbench config. Bootstraps from template on a new machine.
step_gitconfig() {
  _gitconfig_bootstrap
  _gitconfig_ensure_include "$GIT_SHARED_CONFIG"
  success "gitconfig includes up to date"
}

# step_global_hooks — symlinks the workbench pre-commit hook into $GIT_HOOKS_DIR
# and sets git's global core.hooksPath so every repo on this machine is protected.
step_global_hooks() {
  mkdir -p "$GIT_HOOKS_DIR"
  install_symlink "$GIT_HOOKS_SRC_DIR/pre-commit"      "$GIT_HOOKS_DIR/pre-commit"
  install_symlink "$GIT_HOOKS_SRC_DIR/pre-push-global" "$GIT_HOOKS_DIR/pre-push"
  git config --global core.hooksPath "$GIT_HOOKS_DIR"
  success "global core.hooksPath → $GIT_HOOKS_DIR"
}

# sync_git — runs all git sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_git() {
  echo; info "git config → $GITCONFIG_FILE"
  step_gitconfig

  echo; info "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks
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
