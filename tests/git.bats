#!/usr/bin/env bats
# Tests for the git configuration setup (2-layer architecture).

setup() {
  load 'test_helper'
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Source steps.sh for access to helper functions
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/git/steps.sh"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
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

# ── Hooks ────────────────────────────────────────────────────────────────────

@test "pre-commit hook source exists" {
  [ -f "$GIT_HOOKS_SRC_DIR/pre-commit" ]
}

@test "pre-push-global hook source exists" {
  [ -f "$GIT_HOOKS_SRC_DIR/pre-push-global" ]
}

@test "pre-commit hook has current header" {
  grep -q "git/steps.sh" "$GIT_HOOKS_SRC_DIR/pre-commit"
  run grep "task dev:setup" "$GIT_HOOKS_SRC_DIR/pre-commit"
  [ "$status" -ne 0 ]
}

@test "pre-push hook has current header" {
  run grep "task dev:setup" "$GIT_HOOKS_SRC_DIR/pre-push"
  [ "$status" -ne 0 ]
}

# ── Multi-identity helpers ──────────────────────────────────────────────────

@test "write_identity_config creates identity file with user section" {
  GIT_IDENTITY_DIR="$TMPDIR/identities"

  local result
  result="$(_git_write_identity_config "work" "Work User" "work@company.com" "ABCD1234")"

  [ -f "$result" ]
  grep -q 'name = Work User' "$result"
  grep -q 'email = work@company.com' "$result"
  grep -q 'signingKey = ABCD1234' "$result"
}

@test "write_identity_config omits signingKey when empty" {
  GIT_IDENTITY_DIR="$TMPDIR/identities"

  local result
  result="$(_git_write_identity_config "personal" "Personal User" "me@home.com")"

  [ -f "$result" ]
  grep -q 'name = Personal User' "$result"
  grep -q 'email = me@home.com' "$result"
  run grep 'signingKey' "$result"
  [ "$status" -ne 0 ]
}

@test "write_identity_config creates identity directory" {
  GIT_IDENTITY_DIR="$TMPDIR/new-dir/identities"

  _git_write_identity_config "test" "Test" "test@test.com" > /dev/null

  [ -d "$GIT_IDENTITY_DIR" ]
}

@test "ensure_includeif adds stanza for directory" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_includeif "$HOME/git/work" "/path/to/work.gitconfig"

  grep -q 'includeIf "gitdir:'"$HOME"'/git/work/"' "$fake_gitconfig"
  grep -q 'path = /path/to/work.gitconfig' "$fake_gitconfig"
}

@test "ensure_includeif is idempotent" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_includeif "$HOME/git/work/" "/path/to/work.gitconfig"
  _gitconfig_ensure_includeif "$HOME/git/work/" "/path/to/work.gitconfig"

  local count
  count=$(grep -c 'includeIf' "$fake_gitconfig")
  [ "$count" -eq 1 ]
}

@test "ensure_includeif normalizes trailing slash" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  # Pass without trailing slash
  _gitconfig_ensure_includeif "$HOME/git/work" "/path/to/work.gitconfig"

  # Should have trailing slash in the gitdir pattern
  grep -q 'gitdir:'"$HOME"'/git/work/' "$fake_gitconfig"
}

@test "apply_template creates gitconfig with template content" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"

  _gitconfig_apply_template

  [ -f "$GITCONFIG_FILE" ]
  grep -q '\[user\]' "$GITCONFIG_FILE"
}

@test "set_default_identity substitutes placeholders" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  cp "$GIT_CONFIG_TEMPLATE" "$GITCONFIG_FILE"

  _gitconfig_set_default_identity "Test User" "test@example.com" "KEY123"

  grep -q 'name = Test User' "$GITCONFIG_FILE"
  grep -q 'email = test@example.com' "$GITCONFIG_FILE"
  grep -q 'signingKey = KEY123' "$GITCONFIG_FILE"
}

@test "template documents multi-identity pattern" {
  grep -q 'includeIf' "$GIT_CONFIG_TEMPLATE"
  grep -q 'identities' "$GIT_CONFIG_TEMPLATE"
}
