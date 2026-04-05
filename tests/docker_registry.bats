#!/usr/bin/env bats
# Cross-validates docker/registry.yml defaults against docker/colima/aliases.zsh.
# Ensures the two files stay in sync (SSOT guard).

setup() {
  load 'test_helper'
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
