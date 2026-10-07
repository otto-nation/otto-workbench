#!/usr/bin/env bats
# Tests for the 2-layer git configuration: bootstrap, include stanza, template.
# GITCONFIG_FILE is assigned per case and read by the git/steps.sh functions
# under test, which ShellCheck cannot see from here.
# shellcheck disable=SC2034
# The _git_detect_credential_helper stubs replace the real function for the
# step under test, which calls them; ShellCheck sees only the definitions.
# shellcheck disable=SC2329

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"

  # Source steps.sh for access to helper functions
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/git/steps.sh"
}

teardown() {
  cd "$ORIG_DIR" || return 1
  common_teardown
}

# ── Bootstrap ────────────────────────────────────────────────────────────────

@test "bootstrap creates gitconfig from template when missing" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_bootstrap

  [ -f "$fake_gitconfig" ]
  grep -q '\[user\]' "$fake_gitconfig"
}

@test "bootstrap does not overwrite existing gitconfig" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "existing content" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_bootstrap

  grep -q "existing content" "$fake_gitconfig"
}

# ── Include stanza ───────────────────────────────────────────────────────────

@test "ensure_include adds shared config include when missing" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  grep -q "path = /some/path/gitconfig.shared" "$fake_gitconfig"
}

@test "ensure_include is idempotent" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  printf '[include]\n\tpath = /some/path/gitconfig.shared\n' > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  local count
  count=$(grep -c "path = /some/path/gitconfig.shared" "$fake_gitconfig")
  [ "$count" -eq 1 ]
}

@test "ensure_include preserves existing content" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  cat > "$fake_gitconfig" <<'EOF'
[user]
	name = Test User
	email = test@example.com
EOF
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  grep -q "name = Test User" "$fake_gitconfig"
  grep -q "path = /some/path/gitconfig.shared" "$fake_gitconfig"
}

# ── Architecture (live machine) ──────────────────────────────────────────────

@test "shared config file exists and is non-empty" {
  [ -f "$GIT_SHARED_CONFIG" ]
  [ -s "$GIT_SHARED_CONFIG" ]
}

@test "shared config has documentation header" {
  grep -q "Architecture:" "$GIT_SHARED_CONFIG"
}

@test "shared config does not contain machine-specific sections" {
  run grep '^\[user\]' "$GIT_SHARED_CONFIG"
  [ "$status" -ne 0 ]
  run grep '^\[credential\]' "$GIT_SHARED_CONFIG"
  [ "$status" -ne 0 ]
}

# ── Template ─────────────────────────────────────────────────────────────────

@test "gitconfig template exists and is non-empty" {
  [ -f "$GIT_CONFIG_TEMPLATE" ]
  [ -s "$GIT_CONFIG_TEMPLATE" ]
}

@test "template contains user section" {
  grep -q '\[user\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template contains gpg section" {
  grep -q '\[gpg\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template contains credential section" {
  grep -q '\[credential\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template documents the 2-layer architecture" {
  grep -q 'gitconfig.shared' "$GIT_CONFIG_TEMPLATE"
}

# ── Credential helper ────────────────────────────────────────────────────────

# _no_gcm — points every GCM lookup at a path that does not exist, so the
# detector's answer depends only on what PATH holds.
_no_gcm() {
  _git_detect_brew_prefix() { echo "$TMPDIR/no-brew"; }
  GIT_GCM_PKG_PATH="$TMPDIR/no-pkg/git-credential-manager"
}

@test "detect_credential_helper falls back to gh when GCM is absent" {
  _no_gcm
  mkdir -p "$TMPDIR/bin"
  printf '#!/bin/sh\n' > "$TMPDIR/bin/gh"
  chmod +x "$TMPDIR/bin/gh"
  PATH="$TMPDIR/bin:/usr/bin:/bin"

  run _git_detect_credential_helper

  [ "$status" -eq 0 ]
  [ "$output" = "!$TMPDIR/bin/gh auth git-credential" ]
}

@test "detect_credential_helper prints nothing with neither GCM nor gh" {
  _no_gcm
  mkdir -p "$TMPDIR/empty"

  # An empty PATH dir: gh may be installed in /usr/bin on a CI runner.
  PATH="$TMPDIR/empty" run _git_detect_credential_helper

  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "bootstrap writes the detected helper, not the template's macOS path" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  _git_detect_credential_helper() { echo "!/usr/bin/gh auth git-credential"; }

  _gitconfig_bootstrap

  run git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '\n!/usr/bin/gh auth git-credential')" ]
}

@test "bootstrap drops the placeholder helper when none is detected" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  _git_detect_credential_helper() { :; }
  _git_detect_gpg_program() { :; }

  _gitconfig_bootstrap

  run git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
  run git config --file "$GITCONFIG_FILE" gpg.program
  [ "$status" -eq 1 ]
}

@test "repair replaces a helper path that is not executable" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '[credential]\n\thelper =\n\thelper = %s\n[credential "https://dev.azure.com"]\n\tuseHttpPath = true\n' \
    "$TMPDIR/missing/git-credential-manager" > "$GITCONFIG_FILE"
  _git_detect_credential_helper() { echo "!/usr/bin/gh auth git-credential"; }

  run _gitconfig_repair_credential_helper

  [ "$status" -eq 0 ]
  run git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$output" = "$(printf '\n!/usr/bin/gh auth git-credential')" ]
  [ "$(git config --file "$GITCONFIG_FILE" credential.https://dev.azure.com.useHttpPath)" = "true" ]
}

@test "repair works without git config --fixed-value (git < 2.30)" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '[credential]\n\thelper =\n\thelper = %s\n' "$TMPDIR/missing/gcm" > "$GITCONFIG_FILE"
  _git_detect_credential_helper() { echo "!/usr/bin/gh auth git-credential"; }
  git() {
    local a
    for a in "$@"; do [[ "$a" == --fixed-value ]] && return 129; done
    command git "$@"
  }

  run _gitconfig_repair_credential_helper

  [ "$status" -eq 0 ]
  run command git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$output" = "$(printf '\n!/usr/bin/gh auth git-credential')" ]
}

@test "repair does not duplicate a replacement that is already configured" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '[credential]\n\thelper = %s\n\thelper = !/usr/bin/gh auth git-credential\n' \
    "$TMPDIR/missing/gcm" > "$GITCONFIG_FILE"
  _git_detect_credential_helper() { echo "!/usr/bin/gh auth git-credential"; }

  run _gitconfig_repair_credential_helper

  [ "$status" -eq 0 ]
  run git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$output" = "!/usr/bin/gh auth git-credential" ]
}

@test "repair removes a missing helper path when nothing can replace it" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '[credential]\n\thelper =\n\thelper = %s\n' "$TMPDIR/missing/gcm" > "$GITCONFIG_FILE"
  _git_detect_credential_helper() { :; }

  run _gitconfig_repair_credential_helper

  [ "$status" -eq 0 ]
  run git config --file "$GITCONFIG_FILE" --get-all credential.helper
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
}

@test "repair leaves executable, shell, and bare-name helpers alone" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '#!/bin/sh\n' > "$TMPDIR/helper"
  chmod +x "$TMPDIR/helper"
  printf '[credential]\n\thelper = %s\n\thelper = !gh auth git-credential\n\thelper = osxkeychain\n' \
    "$TMPDIR/helper" > "$GITCONFIG_FILE"
  cp "$GITCONFIG_FILE" "$TMPDIR/before"
  _git_detect_credential_helper() { echo "SHOULD-NOT-APPEAR"; }

  run _gitconfig_repair_credential_helper

  [ "$status" -eq 0 ]
  cmp "$TMPDIR/before" "$GITCONFIG_FILE"
}

# ── Placeholder identity ─────────────────────────────────────────────────────
# The template lands without a prompt on a machine that cannot answer one, and
# a placeholder signing key fails every commit. sync has to say so.

@test "the placeholders checked are exactly what the template writes" {
  # A second spelling of the template's values would pass while the template
  # moved on and the warning went quiet. Read them back off a real bootstrap.
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  _gitconfig_bootstrap
  local entry
  for entry in "${GITCONFIG_PLACEHOLDERS[@]}"; do
    [ "$(git config --file "$GITCONFIG_FILE" --get "${entry%%=*}")" = "${entry#*=}" ]
  done
}

@test "a freshly bootstrapped gitconfig warns for every identity key" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  _gitconfig_bootstrap
  warn() { echo "WARN $*"; }
  run _gitconfig_warn_placeholders
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN git identity still has template placeholders"* ]]
  [[ "$output" == *"user.name"* ]]
  [[ "$output" == *"user.email"* ]]
  [[ "$output" == *"user.signingkey"* ]]
}

@test "only the keys still holding a placeholder are named" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  _gitconfig_bootstrap
  git config --file "$GITCONFIG_FILE" user.name "Real Person"
  git config --file "$GITCONFIG_FILE" user.email "real@person.dev"
  warn() { echo "WARN $*"; }
  run _gitconfig_warn_placeholders
  [ "$status" -eq 0 ]
  [[ "$output" == *"user.signingkey"* ]]
  [[ "$output" != *"user.name"* ]]
  [[ "$output" != *"user.email"* ]]
}

@test "a real identity is silent" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  printf '[user]\n\tname = Real Person\n\temail = real@person.dev\n\tsigningKey = ABCDEF0123456789\n' \
    > "$GITCONFIG_FILE"
  warn() { echo "WARN $*"; }
  run _gitconfig_warn_placeholders
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "step_gitconfig runs the placeholder check" {
  # The check is only worth anything if sync reaches it.
  declare -f step_gitconfig | grep -q _gitconfig_warn_placeholders
}
