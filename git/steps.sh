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
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# _gitconfig_write — creates a fresh ~/.gitconfig with both include stanzas.
# Only called when the file does not yet exist.
_gitconfig_write() {
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
	path = $GIT_SHARED_CONFIG

[include]
	path = $GITCONFIG_LOCAL_FILE
EOF
  success "Created $GITCONFIG_FILE"
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

# _gitconfig_bootstrap_local — copies the local config template into place when
# ~/.gitconfig.local does not yet exist. Warns the user to fill in their identity.
_gitconfig_bootstrap_local() {
  if [[ ! -f "$GITCONFIG_LOCAL_FILE" ]]; then
    cp "$GIT_LOCAL_CONFIG_TEMPLATE" "$GITCONFIG_LOCAL_FILE"
    warn "Created $GITCONFIG_LOCAL_FILE from template — edit it to set your identity and credential helpers"
  else
    success ".gitconfig.local already exists"
  fi
}

# step_gitconfig — sets up ~/.gitconfig with [include] lines for the shared
# workbench config and ~/.gitconfig.local. Bootstraps the local file from
# template on a new machine.
step_gitconfig() {
  if [[ ! -f "$GITCONFIG_FILE" ]]; then
    _gitconfig_write
  else
    _gitconfig_ensure_include "$GIT_SHARED_CONFIG"
    _gitconfig_ensure_include "$GITCONFIG_LOCAL_FILE"
    success "gitconfig includes up to date"
  fi

  _gitconfig_bootstrap_local
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
