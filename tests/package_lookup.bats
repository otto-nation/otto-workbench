#!/usr/bin/env bats
# Tests for lib/packages.sh — package_lookup against a fixture manifest tree.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export PACKAGES_DIR="$TMPDIR/packages"
  mkdir -p "$PACKAGES_DIR/tools"
  cat > "$PACKAGES_DIR/Brewfile" <<'EOF'
brew "worktrunk"  # mise: worktrunk; cmd: wt
brew "tree"  # linux: system
brew "pytest"  # mise: pipx:pytest[uvx_args=--with pytest-xdist]
cask "zed"
EOF
  cat > "$PACKAGES_DIR/tools/tools.Brewfile" <<'EOF'
brew "schpet/tap/linear"  # mise: github:schpet/linear-cli
brew "mas"  # macos-only
brew "zsh-completions"  # git: https://github.com/zsh-users/zsh-completions
EOF
  . "$REPO_ROOT/lib/packages.sh"
}

teardown() {
  common_teardown
}

@test "short name resolves a tap formula" {
  declare -A p=(); package_lookup linear p
  [ "${p[formula]}" = "schpet/tap/linear" ]
  [ "${p[mise]}" = "github:schpet/linear-cli" ]
  [ "${p[cmd]}" = "linear" ]
  [ "${p[disposition]}" = "mise" ]
  [ "${p[manifest]}" = "$PACKAGES_DIR/tools/tools.Brewfile" ]
}

@test "cmd overrides the binary name" {
  declare -A p=(); package_lookup worktrunk p; [ "${p[cmd]}" = "wt" ]
}

@test "a spec containing spaces survives" {
  declare -A p=(); package_lookup pytest p
  [ "${p[mise]}" = "pipx:pytest[uvx_args=--with pytest-xdist]" ]
}

@test "linux: system disposition" {
  declare -A p=(); package_lookup tree p
  [ "${p[disposition]}" = "system" ]
  [ -z "${p[mise]}" ]
}

@test "macos-only flag" {
  declare -A p=(); package_lookup mas p; [ "${p[disposition]}" = "macos-only" ]
}

@test "git disposition carries the url" {
  declare -A p=(); package_lookup zsh-completions p
  [ "${p[disposition]}" = "git" ]
  [ "${p[git]}" = "https://github.com/zsh-users/zsh-completions" ]
}

@test "cask is implicitly macos-only" {
  declare -A p=(); package_lookup zed p
  [ "${p[type]}" = "cask" ]
  [ "${p[disposition]}" = "macos-only" ]
}

@test "full formula name also resolves" {
  declare -A p=(); package_lookup schpet/tap/linear p; [ "${p[name]}" = "linear" ]
}

@test "unknown name returns 1" {
  declare -A p=(); run package_lookup nope p; [ "$status" -eq 1 ]
}

@test "name match is whole, not substring" {
  declare -A p=(); run package_lookup ree p; [ "$status" -eq 1 ]
}

@test "unknown annotation tokens are recorded" {
  printf 'brew "odd"  # mise: odd; foo: bar\n' > "$PACKAGES_DIR/Brewfile"
  declare -A p=(); package_lookup odd p
  [ "${p[unknown]}" = "foo: bar" ]
  [ "${p[disposition]}" = "mise" ]
}

@test "odd whitespace still parses" {
  printf 'brew\t"jq"  #  mise : jq ;  cmd:  jq  \n' > "$PACKAGES_DIR/Brewfile"
  declare -A p=(); package_lookup jq p
  [ "${p[mise]}" = "jq" ]
  [ "${p[cmd]}" = "jq" ]
  [ "${p[disposition]}" = "mise" ]
}

@test "package_entries preserves file order" {
  printf '%s\n' 'brew "tree"  # linux: system' 'brew "worktrunk"  # mise: worktrunk; cmd: wt' 'cask "zed"' \
    > "$PACKAGES_DIR/Brewfile"
  local -a names=()
  package_entries "$PACKAGES_DIR/Brewfile" names
  [ "${#names[@]}" -eq 3 ]
  [ "${names[0]}" = "tree" ]
  [ "${names[1]}" = "worktrunk" ]
  [ "${names[2]}" = "zed" ]
}

@test "a cask annotation is dropped from mise and git" {
  printf 'cask "zed"  # mise: zed; git: https://example.com/zed\n' > "$PACKAGES_DIR/Brewfile"
  declare -A p=(); package_lookup zed p
  [ "${p[type]}" = "cask" ]
  [ "${p[disposition]}" = "macos-only" ]
  [ -z "${p[mise]}" ]
  [ -z "${p[git]}" ]
  [ "${p[cask_annotation]}" = "1" ]
}
