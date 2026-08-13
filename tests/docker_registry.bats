#!/usr/bin/env bats
# Cross-validates docker/registry.yml defaults against docker/colima/aliases.zsh.
# Ensures the two files stay in sync (SSOT guard).

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "colima aliases.zsh defaults match docker/registry.yml defaults" {
  local registry="$REPO_ROOT/docker/registry.yml"
  local aliases="$REPO_ROOT/docker/colima/aliases.zsh"

  [[ -f "$registry" ]] || skip "docker/registry.yml not found"
  [[ -f "$aliases" ]]  || skip "docker/colima/aliases.zsh not found"

  local count
  count=$(yq '.env | length' "$registry")

  local i var registry_default aliases_default
  for (( i=0; i<count; i++ )); do
    var=$(yq ".env[$i].var" "$registry")
    registry_default=$(yq ".env[$i].default" "$registry")

    # Extract the matching default from aliases.zsh: ': "${VAR:=value}"'
    aliases_default=$(sed -n "s/.*\${${var}:=\(.*\)}.*/\1/p" "$aliases")

    # An entry with no default is left to runtime detection in aliases.zsh.
    # A registry default is rendered into ~/.env.local as a live export, so it
    # would win over detection — assert the inverse for these vars.
    if [[ "$registry_default" == "null" ]]; then
      [[ -z "$aliases_default" ]] || {
        echo "$var has no registry default but aliases.zsh pins '$aliases_default'" >&2
        return 1
      }
      continue
    fi

    [[ -n "$aliases_default" ]] || {
      echo "$var is in registry.yml but not in aliases.zsh" >&2
      return 1
    }

    [[ "$registry_default" == "$aliases_default" ]] || {
      echo "$var default mismatch: registry='$registry_default' aliases='$aliases_default'" >&2
      return 1
    }
  done
}

@test "COLIMA_ARCH has no registry default so detection is not overridden" {
  local registry="$REPO_ROOT/docker/registry.yml"
  [[ -f "$registry" ]] || skip "docker/registry.yml not found"

  # A default is rendered into ~/.env.local as a live `export`, which would pin
  # every machine to one architecture regardless of the host it runs on.
  local arch_default
  arch_default=$(yq '.env[] | select(.var == "COLIMA_ARCH") | .default' "$registry")
  [ "$arch_default" = "null" ]
}

@test "COLIMA_ARCH is detected from uname when unset" {
  local aliases="$REPO_ROOT/docker/colima/aliases.zsh"
  [[ -f "$aliases" ]] || skip "docker/colima/aliases.zsh not found"
  command -v zsh >/dev/null 2>&1 || skip "zsh not available"

  local machine expected
  machine=$(uname -m)
  case "$machine" in
    arm64 | aarch64) expected=aarch64 ;;
    *) expected=x86_64 ;;
  esac

  run zsh -c "unset COLIMA_ARCH; source '$aliases'; print -r -- \$COLIMA_ARCH"
  [ "$status" -eq 0 ]
  [ "$output" = "$expected" ]
}

@test "COLIMA_ARCH set in the environment survives sourcing" {
  local aliases="$REPO_ROOT/docker/colima/aliases.zsh"
  [[ -f "$aliases" ]] || skip "docker/colima/aliases.zsh not found"
  command -v zsh >/dev/null 2>&1 || skip "zsh not available"

  run zsh -c "export COLIMA_ARCH=riscv64; source '$aliases'; print -r -- \$COLIMA_ARCH"
  [ "$status" -eq 0 ]
  [ "$output" = "riscv64" ]
}
