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

# ─── Helpers ──────────────────────────────────────────────────────────────────

# _git_detect_brew_prefix — returns the Homebrew prefix for the current architecture.
_git_detect_brew_prefix() {
  if command -v brew &>/dev/null; then
    brew --prefix
  elif [[ -d /opt/homebrew ]]; then
    echo "/opt/homebrew"
  else
    echo "/usr/local"
  fi
}

# _git_detect_gpg_program — returns the path to gpg if installed.
_git_detect_gpg_program() {
  local prefix
  prefix="$(_git_detect_brew_prefix)"
  local gpg_path="$prefix/bin/gpg"
  if [[ -x "$gpg_path" ]]; then
    echo "$gpg_path"
  elif command -v gpg &>/dev/null; then
    command -v gpg
  fi
}

# _git_detect_credential_helper — returns the GCM path if installed.
_git_detect_credential_helper() {
  local prefix
  prefix="$(_git_detect_brew_prefix)"
  local gcm_path="$prefix/share/gcm-core/git-credential-manager"
  if [[ -x "$gcm_path" ]]; then
    echo "$gcm_path"
  fi
}

# _gitconfig_interactive_bootstrap — prompts for identity and creates ~/.gitconfig.
# If the file exists, offers overwrite/backup/skip via prompt_overwrite.
# On skip, returns 1 so the caller can fall through to include/hooks only.
_gitconfig_interactive_bootstrap() {
  if [[ -f "$GITCONFIG_FILE" ]]; then
    prompt_overwrite "$GITCONFIG_FILE" || return 1
  fi

  cp "$GIT_CONFIG_TEMPLATE" "$GITCONFIG_FILE"

  echo
  info "Configure your git identity:"
  local user_name user_email signing_key
  read -rp "  Name: " user_name
  read -rp "  Email: " user_email

  echo
  info "GPG signing key (optional — press Enter to skip):"
  echo -e "  ${DIM}Run: gpg --list-secret-keys --keyid-format LONG${NC}"
  read -rp "  Signing key fingerprint: " signing_key

  # Auto-detect machine-specific paths
  local gpg_program credential_helper
  gpg_program="$(_git_detect_gpg_program)"
  credential_helper="$(_git_detect_credential_helper)"

  # Substitute placeholders in the copied template
  if [[ -n "$user_name" ]]; then
    sed -i '' "s|name = Your Name|name = $user_name|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$user_email" ]]; then
    sed -i '' "s|email = you@example.com|email = $user_email|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$signing_key" ]]; then
    sed -i '' "s|signingKey = YOUR_SIGNING_KEY|signingKey = $signing_key|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$gpg_program" ]]; then
    sed -i '' "s|program = /opt/homebrew/bin/gpg|program = $gpg_program|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$credential_helper" ]]; then
    sed -i '' "s|helper = /opt/homebrew/share/gcm-core/git-credential-manager|helper = $credential_helper|" "$GITCONFIG_FILE"
  fi

  success "Created $GITCONFIG_FILE"
  if [[ -z "$signing_key" ]]; then
    warn "No signing key set — edit $GITCONFIG_FILE later to add one"
  fi
}

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

# install_git — interactive setup path for gitconfig.
# Called by install.sh (prefers install_<name> over sync_<name> for core components).
# Prompts for identity and offers overwrite/backup for existing configs.
install_git() {
  echo; info "git config → $GITCONFIG_FILE"
  if _gitconfig_interactive_bootstrap; then
    _gitconfig_ensure_include "$GIT_SHARED_CONFIG"
    success "gitconfig includes up to date"
  else
    # User skipped overwrite — still ensure the include is present.
    _gitconfig_ensure_include "$GIT_SHARED_CONFIG"
    skip "gitconfig identity (kept existing)"
  fi

  echo; info "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks
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

  install_git

  echo
  success "Git setup complete!"
fi
