#!/usr/bin/env bash
# Migration: rename the brew component's install state to packages.
#
# The brew/ component became packages/ when it gained a mise backend for
# machines without Homebrew. Saved stack selections live under
# components.brew in install.yml; without this they would be dropped and the
# next non-interactive install would replay nothing.
#
# adoption-sensitive: install.yml lives under the state root, which legacy-root
# adoption can re-seed with a pre-rename copy after this has been recorded.

migration_20261013_rename_brew_component() {
  [[ -f "$INSTALL_YML_FILE" ]] || return "$MIGRATION_DEFERRED"
  [[ "$(yq '.components | has("brew")' "$INSTALL_YML_FILE")" == true ]] \
    || return "$MIGRATION_NOOP"
  info "Renaming brew component state to packages in install.yml"
  # Overlay packages onto brew so keys only the old name held are kept, and
  # colliding keys prefer the renamed component's value.
  yq -i '.components.packages = ((.components.brew // {}) * (.components.packages // {}))
         | del(.components.brew)' "$INSTALL_YML_FILE"
}
