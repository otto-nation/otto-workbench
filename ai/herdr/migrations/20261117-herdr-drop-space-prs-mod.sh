#!/usr/bin/env bash
# Migration: remove the herdr-app `space-prs` mod, retired with the Space PRs
# sidebar.
#
# The installer copies every mod in the source tree and never deletes one that
# left it, so the old copy would stay installed. Only a copy the workbench
# installed (it carries a .workbench-install manifest) is removed; a hand-made
# mod of that name is left alone. Covers both the trusted window's app directory
# and the untrusted one.

migration_20261117_herdr_drop_space_prs_mod() {
  local home dir removed=0
  for home in "$HERDR_APP_DIR" "$HERDR_UNTRUSTED_APP_DIR"; do
    dir="$home/mods/space-prs"
    [[ -f "$dir/.workbench-install" ]] || continue
    rm -rf "$dir" || { warn "Could not remove $dir"; return 1; }
    removed=1
  done
  (( removed )) || return "$MIGRATION_NOOP"
  success "herdr-app: removed the retired space-prs mod"
}
