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
#   4. Keeps GitHub's SSH connection alive across a long pre-push, and routes it
#      over port 443 when github.ssh_over_443 asks for it
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
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
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

# ─── GitHub SSH ───────────────────────────────────────────────────────────────

# The markers around the block step_github_ssh owns in ~/.ssh/config.
# Everything between them belongs to the workbench and is rewritten as the
# config changes; everything outside is the user's and is never read.
SSH_GITHUB_BEGIN="# >>> otto-workbench: github-ssh >>>"
SSH_GITHUB_END="# <<< otto-workbench: github-ssh <<<"

# _ssh_github_block ROUTE_443 — the managed block's text, on stdout. ROUTE_443
# is `true` when the block should also send github.com to the port 443 endpoint.
#
# The keepalive is in every rendering and the routing is in only some, because
# the two answer to different things: the port is a property of the network the
# machine is on, and the keepalive is a property of how long this machine's
# pre-push hook holds a connection open before git sends anything down it.
_ssh_github_block() {
  local route_443="$1"

  echo "$SSH_GITHUB_BEGIN"
  cat <<EOF
# git opens the connection to the remote before it runs pre-push and sends the
# packfile only once the hook returns, so the socket sits idle for as long as
# the gates take. A hook that outlasts the server's idle timeout loses the push
# after every check has already passed. A keepalive every 30 seconds keeps the
# connection from being judged idle, and ten unanswered keepalives — five
# minutes of silence — is what it takes to call the connection dead.
EOF
  if [[ "$route_443" == true ]]; then
    cat <<EOF
# Some networks block or intermittently reset outbound TCP/22. GitHub serves
# the same SSH endpoint, with the same host keys, on port 443.
EOF
  fi
  cat <<EOF
# Managed by otto-workbench — $GITHUB_SSH_443_CONFIG_KEY in
# $WORKBENCH_CONFIG_NAME decides the port, and the keepalive is not optional.
# Edit the config and re-sync rather than this block.
Host github.com
EOF
  if [[ "$route_443" == true ]]; then
    cat <<EOF
  Hostname ssh.github.com
  Port 443
EOF
  fi
  cat <<EOF
  ServerAliveInterval 30
  ServerAliveCountMax 10
$SSH_GITHUB_END
EOF
}

# _ssh_github_write FILE — replace FILE's contents with stdin.
#
# The scratch file is colocated with the destination so the mv is a rename
# within one filesystem rather than a copy that can be seen half-written, and it
# carries mode 600 before the move so the config is never briefly readable by
# anyone else.
_ssh_github_write() {
  local file="$1" tmp
  tmp="$(mktemp "$file.XXXXXX")"
  cat > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$file"
}

# _ssh_github_strip FILE — FILE with the managed block removed, on stdout.
_ssh_github_strip() {
  awk -v begin="$SSH_GITHUB_BEGIN" -v end="$SSH_GITHUB_END" '
    $0 == begin { dropping = 1 }
    dropping != 1 { print }
    $0 == end { dropping = 0 }
  ' "$1"
}

# _ssh_github_current FILE — the managed block as FILE currently spells it, on
# stdout. Empty when FILE has no block. Compared against _ssh_github_block so a
# machine that installed an older wording — or one whose 443 key has since
# flipped — picks up the current text on sync rather than keeping the stale
# version until someone deletes it by hand.
_ssh_github_current() {
  awk -v begin="$SSH_GITHUB_BEGIN" -v end="$SSH_GITHUB_END" '
    $0 == begin { inside = 1 }
    inside { print }
    $0 == end && inside { exit }
  ' "$1"
}

# _ssh_github_insert FILE ROUTE_443 — write the managed block into FILE, ahead
# of the first Host or Match block.
#
# Placement is the whole job. ssh keeps the first value it reads for each
# keyword, so a Host github.com block sitting after a Host * block loses to it
# and everything in it silently does nothing. Inserting before the first block
# also lands after the Include lines that Colima and OrbStack require at the top
# of the file, which is where they have to stay.
#
# The search is case-insensitive and accepts `=` as the separator because
# ssh_config is: `host *`, `HOST *`, and `Host=*` all open a block, and reading
# only the capitalized spelling would append to the end of a file whose wildcard
# block is lowercase — the exact placement this function exists to avoid.
_ssh_github_insert() {
  local file="$1" route_443="$2" first_block
  first_block="$(grep -n -m1 -iE '^[[:space:]]*(host|match)([[:space:]]|=)' "$file" | cut -d: -f1 || true)"
  if [[ -z "$first_block" ]]; then
    { cat "$file"; echo; _ssh_github_block "$route_443"; } | _ssh_github_write "$file"
  else
    # awk rather than head for the leading part: a config whose very first line
    # opens a block asks for zero lines, and BSD head rejects `-n 0` outright.
    { awk -v n="$((first_block - 1))" 'NR <= n' "$file"; _ssh_github_block "$route_443"; echo; tail -n +"$first_block" "$file"; } | _ssh_github_write "$file"
  fi
}

# _ssh_github_warn_unmanaged FILE — warn when FILE declares github.com outside
# the managed block.
#
# First value wins, so an entry of the user's own decides the connection
# whichever way this step leaves its block: ahead of ours it overrides the port
# and the keepalive alike, and it outlives the rewrite that is supposed to put
# the machine back on port 22. The entry is theirs, so it is reported rather
# than rewritten.
_ssh_github_warn_unmanaged() {
  local file="$1"
  if _ssh_github_strip "$file" | grep -qiE '^[[:space:]]*host([[:space:]]|=)+github\.com([[:space:]]|$)'; then
    warn "$file declares Host github.com outside the managed block — ssh keeps the first value it reads, so that entry decides the connection"
  fi
}

# _ssh_github_known_hosts — teach known_hosts the [ssh.github.com]:443 spelling of
# the GitHub host keys this machine already trusts.
#
# Copied from the machine's own github.com entries rather than scanned off the
# network: port 443 is the same service behind the same keys, so reusing what
# was already accepted adds no trust that was not there, and it needs neither
# gh nor a working connection — which is a poor thing to depend on in the step
# whose reason for existing is that the network is unreliable.
#
# A machine with no github.com entry yet has nothing to copy. That is left
# alone: ssh then asks to confirm the key on the first connection, which is the
# right question to put to a person rather than answer on their behalf.
_ssh_github_known_hosts() {
  local known="$SSH_KNOWN_HOSTS_FILE"
  [[ -f "$known" ]] || return 0
  if grep -qF "[ssh.github.com]:443 " "$known"; then return 0; fi

  local entries
  entries="$(ssh-keygen -F github.com -f "$known" 2>/dev/null | grep -v '^#' || true)"
  if [[ -z "$entries" ]]; then
    warn "known_hosts has no github.com entry — ssh will ask to confirm ssh.github.com on first connect"
    return 0
  fi

  # Field 1 is the host pattern, which may be hashed and is replaced wholesale;
  # fields 2 and 3 are the key type and the key itself, which carry over as-is.
  awk '{ print "[ssh.github.com]:443 " $2 " " $3 }' <<< "$entries" >> "$known"
  success "known_hosts: added [ssh.github.com]:443 from the github.com keys already trusted"
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
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "gitconfig includes up to date" || true
}

# step_global_gitignore — ensures entries from git/gitignore.global are present
# in the global gitignore file (~/.config/git/ignore, set via excludesFile).
step_global_gitignore() {
  local src="$GIT_SRC_DIR/gitignore.global"
  local dest="$HOME/.config/git/ignore"

  [[ -f "$src" ]] || return 0

  mkdir -p "$(dirname "$dest")"
  [[ -f "$dest" ]] || touch "$dest"

  local -a new_entries=()
  while IFS= read -r entry; do
    [[ -z "$entry" || "$entry" == \#* ]] && continue
    if ! grep -qxF "$entry" "$dest"; then
      new_entries+=("$entry")
    fi
  done < "$src"

  local added=${#new_entries[@]}
  if [[ $added -gt 0 ]]; then
    printf '\n# Managed by otto-workbench\n' >> "$dest"
    for entry in "${new_entries[@]}"; do
      printf '%s\n' "$entry" >> "$dest"
    done
    success "global gitignore: added $added entries to $dest"
  else
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "global gitignore up to date" || true
  fi
}

# step_global_hooks — symlinks the workbench pre-commit hook into $GIT_HOOKS_DIR
# and sets git's global core.hooksPath so every repo on this machine is protected.
step_global_hooks() {
  mkdir -p "$GIT_HOOKS_DIR"
  install_symlink "$GIT_HOOKS_SRC_DIR/pre-commit"      "$GIT_HOOKS_DIR/pre-commit"
  install_symlink "$GIT_HOOKS_SRC_DIR/pre-push" "$GIT_HOOKS_DIR/pre-push"
  git config --global core.hooksPath "$GIT_HOOKS_DIR"
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "global core.hooksPath → $GIT_HOOKS_DIR" || true
}

# step_local_hooks — installs repo-local hooks into .git/hooks/ for the workbench repo.
# When core.hooksPath is set globally, git ignores .git/hooks/ entirely.
# The global hooks delegate back to .git/hooks/ if present,
# so this step is required for repo-local hooks to run.
step_local_hooks() {
  # The directory every checkout of this repo shares, which is where its hooks
  # live — in a worktree `.git` is a file, not a directory. lib/git_layout.sh
  # owns the lookup, and answers with an absolute path: `git -C DIR rev-parse
  # --git-common-dir` reports a plain `.git` for an ordinary clone, resolved
  # against the caller's cwd rather than against DIR, so a sync run from
  # anywhere else wrote the dispatchers into a `.git/hooks` it created there.
  local dot_git
  dot_git="$(git_shared_dir "$WORKBENCH_DIR")" || {
    err "local git hooks: git names no git dir for $WORKBENCH_DIR"
    return 1
  }
  mkdir -p "$dot_git/hooks"

  # Auto-heal: if something set core.hooksPath to /dev/null, hooks are silently disabled.
  # Addressed to the workbench checkout for the same reason as above — cwd is
  # whatever the operator ran the sync from, and unsetting the key there would
  # re-enable hooks in somebody else's repository.
  local hooks_path
  hooks_path=$(git -C "$WORKBENCH_DIR" config --local core.hooksPath 2>/dev/null) || true
  if [[ "$hooks_path" == "/dev/null" ]]; then
    git -C "$WORKBENCH_DIR" config --unset core.hooksPath
    warn "removed core.hooksPath=/dev/null from local config (hooks were disabled)"
  fi

  sync_header "local git hooks → .git/hooks/"
  install_hook_dispatcher "git/hooks/pre-commit-workbench" "$dot_git/hooks/pre-commit" "pre-commit"
  install_hook_dispatcher "git/hooks/pre-push-workbench"   "$dot_git/hooks/pre-push"   "pre-push"
}

# step_github_ssh — keep the managed github.com block in ~/.ssh/config saying
# what the workbench currently means it to say.
#
# The block itself is unconditional: the keepalive it carries is what keeps a
# push from being dropped while pre-push runs, and no machine wants that off.
# Port 443 routing is the part the config decides — see _ssh_github_block for
# why it defaults off — and flipping the key back takes the Hostname and Port
# lines out again, which is what keeps the setting from being a one-way door.
step_github_ssh() {
  local route_443 present=false route_note="direct (port 22)"
  route_443="$(wb_config_get "$GITHUB_SSH_443_CONFIG_KEY" false)"
  if [[ "$route_443" == true ]]; then
    route_note="over port 443 (ssh.github.com)"
  fi

  if [[ -f "$SSH_CONFIG_FILE" ]] && grep -qF "$SSH_GITHUB_BEGIN" "$SSH_CONFIG_FILE"; then
    present=true
  fi

  # A begin marker with no end marker means someone edited the block by hand and
  # left it open. Stripping from the begin marker to end-of-file would take the
  # rest of their config with it, so the file is reported and left alone.
  if [[ "$present" == true ]] && ! grep -qF "$SSH_GITHUB_END" "$SSH_CONFIG_FILE"; then
    warn "$SSH_CONFIG_FILE has the github-ssh begin marker but no end marker — leaving the file untouched"
    return 0
  fi

  if [[ "$present" == true ]]; then
    if [[ "$(_ssh_github_current "$SSH_CONFIG_FILE")" == "$(_ssh_github_block "$route_443")" ]]; then
      [[ "${WORKBENCH_SYNC:-}" != true ]] && success "github SSH: $route_note" || true
      return 0
    fi
    # The block is ours but its text has drifted from what the template renders
    # now — an older release's wording, or the 443 key moving either way. Take
    # it out and put it back rather than patching in place, so the rewrite goes
    # through the one function that knows where the block belongs.
    _ssh_github_strip "$SSH_CONFIG_FILE" | _ssh_github_write "$SSH_CONFIG_FILE"
  else
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    [[ -f "$SSH_CONFIG_FILE" ]] || : > "$SSH_CONFIG_FILE"
  fi

  _ssh_github_insert "$SSH_CONFIG_FILE" "$route_443"
  if [[ "$route_443" == true ]]; then
    _ssh_github_known_hosts
  fi
  _ssh_github_warn_unmanaged "$SSH_CONFIG_FILE"
  success "github SSH: block written — $route_note"
}

# step_worktrunk_config — ensures the global worktrunk config has a default
# worktree-path so bare repos get clean directory names instead of .git.* prefix.
step_worktrunk_config() {
  command -v wt >/dev/null 2>&1 || return 0

  local config_file="$WORKTRUNK_CONFIG_FILE"
  local desired='worktree-path = "{{ repo_path }}/../{{ branch | sanitize }}"'

  if [[ -f "$config_file" ]] && grep -q '^worktree-path' "$config_file"; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "worktrunk worktree-path already set" || true
    return 0
  fi

  mkdir -p "$(dirname "$config_file")"
  if [[ -f "$config_file" ]]; then
    # Prepend before any [sections] so it's a top-level key
    local tmp
    tmp=$(mktemp)
    { echo "$desired"; echo ""; cat "$config_file"; } > "$tmp"
    mv "$tmp" "$config_file"
  else
    echo "$desired" > "$config_file"
  fi
  success "worktrunk worktree-path default set"
}

# step_worktrunk_pre_switch_fetch — ensures the worktrunk config has a
# pre-switch hook that brings the default branch up to date with origin, so new
# branches are always created from the latest remote HEAD.
#
# The whole command lives in `git/bin/wt-fetch-default` rather than in the hook
# line, because deciding how to move the ref needs a branch the template
# language cannot express, and because the decision is worth testing.
#
# A `fetch-default` line that has drifted from the template is rewritten rather
# than left alone. The line this replaces fast-forwarded through
# `{{ worktree_path_of_branch(default_branch) }}`, which renders empty when no
# worktree holds the branch — every machine carrying it needs the line replaced,
# not merely not re-added, which is why the step converges instead of backing
# off at the first `fetch-default` it sees.
step_worktrunk_pre_switch_fetch() {
  command -v wt >/dev/null 2>&1 || return 0

  local config_file="$WORKTRUNK_CONFIG_FILE"
  local hook_cmd='fetch-default = "wt-fetch-default {{ default_branch }}"'

  if [[ ! -f "$config_file" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && skip "worktrunk config not found — run step_worktrunk_config first" || true
    return 0
  fi

  if grep -qxF "$hook_cmd" "$config_file"; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "worktrunk pre-switch fetch already set" || true
    return 0
  fi

  if grep -q '^fetch-default' "$config_file"; then
    # The key is ours but its command has drifted from the current template.
    local tmp
    tmp=$(mktemp "${config_file}.XXXXXX")
    awk -v line="$hook_cmd" '/^fetch-default/ { print line; next } { print }' "$config_file" > "$tmp"
    mv "$tmp" "$config_file"
    success "worktrunk pre-switch fetch hook refreshed"
    return 0
  fi

  if grep -q '^\[pre-switch\]' "$config_file"; then
    # Append under existing [pre-switch] section
    sed_i '/^\[pre-switch\]/a\
'"$hook_cmd"'' "$config_file"
  else
    # Append new section at end of file
    printf '\n[pre-switch]\n%s\n' "$hook_cmd" >> "$config_file"
  fi
  success "worktrunk pre-switch fetch hook added"
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

  echo; info "global gitignore → ~/.config/git/ignore"
  step_global_gitignore

  echo; info "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks
  step_local_hooks

  echo; info "git scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$GIT_SRC_DIR"

  echo; info "github SSH → $SSH_CONFIG_FILE"
  step_github_ssh

  echo; info "worktrunk config"
  step_worktrunk_config
  step_worktrunk_pre_switch_fetch
}

# sync_git — runs all git sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_git() {
  sync_header "git config → $GITCONFIG_FILE"
  step_gitconfig

  sync_header "global gitignore → ~/.config/git/ignore"
  step_global_gitignore

  sync_header "global git hooks → $GIT_HOOKS_DIR"
  step_global_hooks
  step_local_hooks

  sync_header "git scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$GIT_SRC_DIR"

  sync_header "github SSH → $SSH_CONFIG_FILE"
  step_github_ssh

  sync_header "worktrunk config"
  step_worktrunk_config
  step_worktrunk_pre_switch_fetch
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Git setup${NC}\n"

  install_git

  echo
  success "Git setup complete!"
fi
