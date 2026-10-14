#!/usr/bin/env bats
# Tests for bin/local/validate-manifests.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export PACKAGES_DIR="$TMPDIR/packages"
  export VALIDATE_ROOT="$TMPDIR/root"
  mkdir -p "$PACKAGES_DIR/tools" "$VALIDATE_ROOT"
}

teardown() {
  common_teardown
}

@test "a brew line with no disposition fails" {
  printf 'brew "jq"\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'jq'*'no disposition'* ]]
}

@test "two dispositions fail" {
  printf 'brew "jq"  # mise: jq; macos-only\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'more than one'* ]]
}

@test "a package in two manifests fails" {
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/tools/tools.Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'jq'*'two manifests'* ]]
}

@test "an unknown annotation token fails" {
  printf 'brew "jq"  # mise: jq; foo: bar\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'unknown annotation token'* ]]
}

@test "an empty mise value fails" {
  printf 'brew "jq"  # mise:\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'empty annotation value'* ]]
}

@test "an empty git value fails" {
  printf 'brew "zsh-foo"  # git:\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'empty annotation value'* ]]
}

@test "an empty cmd value fails" {
  printf 'brew "jq"  # mise: jq; cmd:\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'empty annotation value'* ]]
}

@test "an annotation on a cask fails" {
  printf 'cask "zed"  # mise: zed\n' > "$PACKAGES_DIR/Brewfile"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *'cask lines cannot carry annotations'* ]]
}

@test "install_package naming an unknown package fails" {
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  mkdir -p "$VALIDATE_ROOT/x"
  printf 'install_package nope || true\n' > "$VALIDATE_ROOT/x/steps.sh"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *nope* ]]
}

@test "a quoted install_package literal is scanned" {
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  mkdir -p "$VALIDATE_ROOT/x"
  printf 'install_package "nope" || true\n' > "$VALIDATE_ROOT/x/steps.sh"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *nope* ]]
}

@test "DEV_DEPS_INSTALLABLE names are scanned" {
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  mkdir -p "$VALIDATE_ROOT/bin/local"
  cat > "$VALIDATE_ROOT/bin/local/dev-deps" <<'EOF'
DEV_DEPS_INSTALLABLE=(
  jq
  nope
)
EOF
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *DEV_DEPS_INSTALLABLE*nope* ]]
}

@test "an extensionless ai bin script is scanned" {
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  mkdir -p "$VALIDATE_ROOT/ai/claude/bin"
  printf 'install_package nope || true\n' > "$VALIDATE_ROOT/ai/claude/bin/tool"
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 1 ]
  [[ "$output" == *nope* ]]
}

@test "the real manifests pass" {
  unset PACKAGES_DIR VALIDATE_ROOT
  run "$REPO_ROOT/bin/local/validate-manifests"
  [ "$status" -eq 0 ]
}
