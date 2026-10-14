#!/usr/bin/env bats
# Tests for packages/migrations/20261013-rename-brew-component.sh — moves
# components.brew to components.packages in install.yml.
#
# Sources lib/ui.sh then lib/migrations.sh, matching the restore-dropped-tools
# suite: lib/migrations.sh requires WORKBENCH_DIR and LEGACY_WORKBENCH_ROOT,
# and the status constants stay owned by the framework.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/migrations.sh"
  export INSTALL_YML_FILE="$TMPDIR/install.yml"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/packages/migrations/20261013-rename-brew-component.sh"
}

teardown() {
  common_teardown
}

@test "moves components.brew to components.packages" {
  printf 'components:\n  brew:\n    stacks:\n      - lang/go\n' > "$INSTALL_YML_FILE"
  run migration_20261013_rename_brew_component
  [ "$status" -eq 0 ]
  [ "$(yq '.components.packages.stacks[0]' "$INSTALL_YML_FILE")" = "lang/go" ]
  [ "$(yq '.components | has("brew")' "$INSTALL_YML_FILE")" = "false" ]
}

@test "an existing components.packages wins colliding keys and brew is still removed" {
  printf 'components:\n  brew:\n    stacks: [lang/go]\n  packages:\n    stacks: [infra/aws]\n' > "$INSTALL_YML_FILE"
  run migration_20261013_rename_brew_component
  [ "$status" -eq 0 ]
  [ "$(yq '.components.packages.stacks[0]' "$INSTALL_YML_FILE")" = "infra/aws" ]
  [ "$(yq '.components | has("brew")' "$INSTALL_YML_FILE")" = "false" ]
}

@test "when both maps exist, keys only brew held are kept" {
  printf 'components:\n  brew:\n    stacks: [lang/go]\n    extra: kept\n  packages:\n    stacks: [infra/aws]\n' > "$INSTALL_YML_FILE"
  run migration_20261013_rename_brew_component
  [ "$status" -eq 0 ]
  [ "$(yq '.components.packages.stacks[0]' "$INSTALL_YML_FILE")" = "infra/aws" ]
  [ "$(yq '.components.packages.extra' "$INSTALL_YML_FILE")" = "kept" ]
  [ "$(yq '.components | has("brew")' "$INSTALL_YML_FILE")" = "false" ]
}

@test "no brew key is a no-op" {
  printf 'components:\n  mise: true\n' > "$INSTALL_YML_FILE"
  run migration_20261013_rename_brew_component
  [ "$status" -eq "$MIGRATION_NOOP" ]
}

@test "absent install.yml defers" {
  run migration_20261013_rename_brew_component
  [ "$status" -eq "$MIGRATION_DEFERRED" ]
}
