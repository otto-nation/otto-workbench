#!/usr/bin/env bats
# Tests for resolve_layers() and user override integration in lib/files.sh.

setup() {
  load 'test_helper'
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"

  TMPDIR="$(mktemp -d)"
  BASE_DIR="$TMPDIR/base"
  USER_DIR="$TMPDIR/user"
  mkdir -p "$BASE_DIR" "$USER_DIR"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ─── resolve_layers ──────────────────────────────────────────────────────────

@test "resolve_layers: base only — all files included" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "default" > "$BASE_DIR/bar.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 2 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
  [[ "${result[bar.md]}" == "$BASE_DIR/bar.md" ]]
}

@test "resolve_layers: user file replaces base file with same name" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "override" > "$USER_DIR/foo.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[foo.md]}" == "$USER_DIR/foo.md" ]]
}

@test "resolve_layers: user adds new files not in base" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "new" > "$USER_DIR/custom.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 2 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
  [[ "${result[custom.md]}" == "$USER_DIR/custom.md" ]]
}

@test "resolve_layers: .disabled sentinel suppresses base file" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "default" > "$BASE_DIR/bar.md"
  touch "$USER_DIR/foo.disabled"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[bar.md]}" == "$BASE_DIR/bar.md" ]]
  [[ -z "${result[foo.md]+set}" ]]
}

@test "resolve_layers: empty user dir — falls through to base" {
  echo "default" > "$BASE_DIR/foo.md"
  rmdir "$USER_DIR"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
}

@test "resolve_layers: works with directory glob" {
  mkdir -p "$BASE_DIR/skill-a" "$BASE_DIR/skill-b"
  mkdir -p "$USER_DIR/skill-a"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*/" result

  [[ ${#result[@]} -eq 2 ]]
  # User dir wins for skill-a
  [[ "${result[skill-a]}" == "$USER_DIR/skill-a" ]]
  [[ "${result[skill-b]}" == "$BASE_DIR/skill-b" ]]
}

@test "resolve_layers: .disabled suppresses directory items" {
  mkdir -p "$BASE_DIR/skill-a" "$BASE_DIR/skill-b"
  touch "$USER_DIR/skill-a.disabled"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*/" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[skill-b]}" == "$BASE_DIR/skill-b" ]]
}

# ─── is_disabled ─────────────────────────────────────────────────────────────

@test "is_disabled: returns true when sentinel exists" {
  touch "$USER_DIR/foo.disabled"
  is_disabled "$USER_DIR" "foo"
}

@test "is_disabled: returns false when no sentinel" {
  ! is_disabled "$USER_DIR" "foo"
}
