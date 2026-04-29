#!/usr/bin/env bash
# description: Configure gitconfig, shared settings, and global hooks
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
# Checks the Homebrew prefix first, then /usr/local (pkg installer location),
# then falls back to PATH — the pkg installs to /usr/local regardless of arch.
_git_detect_credential_helper() {
  local prefix
  prefix="$(_git_detect_brew_prefix)"
  local gcm_path="$prefix/share/gcm-core/git-credential-manager"
  if [[ -x "$gcm_path" ]]; then
    echo "$gcm_path"
  elif [[ -x "/usr/local/share/gcm-core/git-credential-manager" ]]; then
    echo "/usr/local/share/gcm-core/git-credential-manager"
  elif command -v git-credential-manager &>/dev/null; then
    command -v git-credential-manager
  fi
}

# _git_prompt_identity LABEL — prompts for name, email, signing key for one identity.
# Sets _ID_NAME, _ID_EMAIL, _ID_KEY in the caller's scope.
_git_prompt_identity() {
  local label="$1"
  info "Configure ${BOLD}${label}${NC} identity:"
  read -rp "  Name: " _ID_NAME
  read -rp "  Email: " _ID_EMAIL

  echo
  info "GPG signing key (optional — press Enter to skip):"
  echo -e "  ${DIM}Run: gpg --list-secret-keys --keyid-format LONG${NC}"
  read -rp "  Signing key fingerprint: " _ID_KEY
}

# _git_write_identity_config LABEL NAME EMAIL [SIGNING_KEY] — writes an identity
# config file to $GIT_IDENTITY_DIR/<label>.gitconfig.
# Returns the path of the written file via stdout.
_git_write_identity_config() {
  local label="$1" name="$2" email="$3" key="${4:-}"
  mkdir -p "$GIT_IDENTITY_DIR"

  local identity_file="$GIT_IDENTITY_DIR/${label}.gitconfig"
  {
    echo "[user]"
    echo "	name = $name"
    echo "	email = $email"
    if [[ -n "$key" ]]; then
      echo "	signingKey = $key"
    fi
  } > "$identity_file"

  echo "$identity_file"
}

# _gitconfig_ensure_includeif GITDIR IDENTITY_FILE — appends an [includeIf]
# stanza for a directory-based identity override. Idempotent.
_gitconfig_ensure_includeif() {
  local gitdir="$1" identity_file="$2"
  [[ "$gitdir" == */ ]] || gitdir="${gitdir}/"
  if ! grep -qF "gitdir:${gitdir}" "$GITCONFIG_FILE" 2>/dev/null; then
    printf '\n[includeIf "gitdir:%s"]\n\tpath = %s\n' "$gitdir" "$identity_file" >> "$GITCONFIG_FILE"
  fi
}

# _gitconfig_apply_template — copies the template and substitutes machine-specific
# paths (GPG, credential helper). Does NOT set identity — that's handled separately.
_gitconfig_apply_template() {
  cp "$GIT_CONFIG_TEMPLATE" "$GITCONFIG_FILE"

  local gpg_program credential_helper
  gpg_program="$(_git_detect_gpg_program)"
  credential_helper="$(_git_detect_credential_helper)"

  if [[ -n "$gpg_program" ]]; then
    sed_i "s|program = /opt/homebrew/bin/gpg|program = $gpg_program|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$credential_helper" ]]; then
    sed_i "s|helper = /opt/homebrew/share/gcm-core/git-credential-manager|helper = $credential_helper|" "$GITCONFIG_FILE"
  fi
}

# _gitconfig_set_default_identity NAME EMAIL [SIGNING_KEY] — writes the default
# identity into ~/.gitconfig by replacing the template placeholders.
_gitconfig_set_default_identity() {
  local name="$1" email="$2" key="${3:-}"
  if [[ -n "$name" ]]; then
    sed_i "s|name = Your Name|name = $name|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$email" ]]; then
    sed_i "s|email = you@example.com|email = $email|" "$GITCONFIG_FILE"
  fi
  if [[ -n "$key" ]]; then
    sed_i "s|signingKey = YOUR_SIGNING_KEY|signingKey = $key|" "$GITCONFIG_FILE"
  fi
}

# _gitconfig_single_identity_flow — prompts for one identity and writes it to
# ~/.gitconfig. Used by both the single-identity path and the <2 labels fallback.
_gitconfig_single_identity_flow() {
  _gitconfig_apply_template
  echo
  _git_prompt_identity "default"
  _gitconfig_set_default_identity "$_ID_NAME" "$_ID_EMAIL" "$_ID_KEY"
  success "Created $GITCONFIG_FILE"
  if [[ -z "$_ID_KEY" ]]; then
    warn "No signing key set — edit $GITCONFIG_FILE later to add one"
  fi
}

# _gitconfig_interactive_bootstrap — prompts for identity and creates ~/.gitconfig.
# If the file exists, offers overwrite/backup/skip via prompt_overwrite.
# On skip, returns 1 so the caller can fall through to include/hooks only.
#
# Supports two flows:
#   Single identity: prompts once, writes directly to ~/.gitconfig (original behavior)
#   Multi identity:  prompts for labels → collects each identity → asks for default
#                    → writes default inline, others as includeIf with directory paths
_gitconfig_interactive_bootstrap() {
  if [[ -f "$GITCONFIG_FILE" ]]; then
    prompt_overwrite "$GITCONFIG_FILE" || return 1
  fi

  echo
  if ! confirm_n "Do you want multiple git identities (e.g. work and personal)?"; then
    _gitconfig_single_identity_flow
    return 0
  fi

  # ── Multi identity flow ──────────────────────────────────────────────
  echo
  info "Enter identity labels separated by spaces"
  echo -e "  ${DIM}Example: work personal${NC}"
  local labels_raw
  read -rp "  Labels: " labels_raw

  local labels=()
  read -ra labels <<< "$labels_raw"

  if [[ ${#labels[@]} -lt 2 ]]; then
    warn "Need at least 2 identities — falling back to single identity"
    _gitconfig_single_identity_flow
    return 0
  fi

  # Collect identities as parallel arrays
  local names=() emails=() keys=()
  local label
  for label in "${labels[@]}"; do
    echo
    _git_prompt_identity "$label"
    names+=("$_ID_NAME")
    emails+=("$_ID_EMAIL")
    keys+=("$_ID_KEY")
  done

  # Ask which identity is the default
  echo
  info "Which identity should be the default?"
  local i
  for i in "${!labels[@]}"; do
    echo -e "  ${BOLD}$((i + 1))${NC}) ${labels[$i]} — ${names[$i]} <${emails[$i]}>"
  done
  local default_choice
  select_menu default_choice "${#labels[@]}" --default require --single
  if [[ -z "$default_choice" ]]; then
    warn "No default selected — using first identity"
    default_choice=1
  fi
  local default_idx=$((default_choice - 1))

  # Collect directory paths for non-default identities
  local gitdirs=()
  for i in "${!labels[@]}"; do
    if [[ "$i" -eq "$default_idx" ]]; then
      gitdirs+=("")
      continue
    fi
    echo
    info "Directory for ${BOLD}${labels[$i]}${NC} repos (includeIf gitdir match):"
    echo -e "  ${DIM}Example: ~/git/work/${NC}"
    local gitdir
    read -rp "  Directory: " gitdir
    # Expand ~ to $HOME so git's includeIf gitdir matching works with absolute paths
    gitdir="${gitdir/#\~/$HOME}"
    gitdirs+=("$gitdir")
  done

  # Write gitconfig
  _gitconfig_apply_template
  _gitconfig_set_default_identity "${names[$default_idx]}" "${emails[$default_idx]}" "${keys[$default_idx]}"

  # Write non-default identity files and includeIf stanzas
  for i in "${!labels[@]}"; do
    [[ "$i" -eq "$default_idx" ]] && continue
    local identity_file
    identity_file="$(_git_write_identity_config "${labels[$i]}" "${names[$i]}" "${emails[$i]}" "${keys[$i]}")"
    _gitconfig_ensure_includeif "${gitdirs[$i]}" "$identity_file"
    success "Identity: ${labels[$i]} → $identity_file"
  done

  success "Created $GITCONFIG_FILE (default: ${labels[$default_idx]})"
  echo
  info "Identity files written to $GIT_IDENTITY_DIR/"

  # Warn about missing signing keys
  for i in "${!labels[@]}"; do
    if [[ -z "${keys[$i]}" ]]; then
      warn "No signing key for '${labels[$i]}' — add one later"
    fi
  done
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
  install_symlink "$GIT_HOOKS_SRC_DIR/pre-push" "$GIT_HOOKS_DIR/pre-push"
  git config --global core.hooksPath "$GIT_HOOKS_DIR"
  success "global core.hooksPath → $GIT_HOOKS_DIR"
}

# step_local_hooks — installs repo-local hooks into .git/hooks/ for the workbench repo.
# When core.hooksPath is set globally, git ignores .git/hooks/ entirely.
# The global hooks delegate back to .git/hooks/ if present,
# so this step is required for repo-local hooks to run.
step_local_hooks() {
  local dot_git="$WORKBENCH_DIR/.git"
  # In a worktree, .git is a file (not a directory) — skip in that case
  [[ -d "$dot_git" ]] || return 0
  mkdir -p "$dot_git/hooks"

  # Auto-heal: if something set core.hooksPath to /dev/null, hooks are silently disabled
  local hooks_path
  hooks_path=$(git config --local core.hooksPath 2>/dev/null) || true
  if [[ "$hooks_path" == "/dev/null" ]]; then
    git config --unset core.hooksPath
    warn "removed core.hooksPath=/dev/null from local config (hooks were disabled)"
  fi

  echo; info "local git hooks → .git/hooks/"
  install_hook_dispatcher "git/hooks/pre-commit-workbench" "$dot_git/hooks/pre-commit" "pre-commit"
  install_hook_dispatcher "git/hooks/pre-push-workbench"   "$dot_git/hooks/pre-push"   "pre-push"
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
  step_local_hooks

  echo; info "git scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$GIT_SRC_DIR"
}

# sync_git — runs all git sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_git() {
  echo; info "git config → $GITCONFIG_FILE"
  step_gitconfig

  echo; info "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks
  step_local_hooks

  echo; info "git scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$GIT_SRC_DIR"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Git setup${NC}\n"

  install_git

  echo
  success "Git setup complete!"
fi
