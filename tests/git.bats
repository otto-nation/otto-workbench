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
