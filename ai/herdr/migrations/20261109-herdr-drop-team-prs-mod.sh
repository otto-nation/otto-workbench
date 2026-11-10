#!/usr/bin/env bash
# Migration: remove the herdr-app `team-prs` mod, renamed to `prs`.
#
# The installer copies every mod in the source tree and never deletes one that
# left it, so the old copy would stay installed beside the new one. Only a copy
# the workbench installed (it carries a .workbench-install manifest) is removed;
# a hand-made mod of that name is left alone. Covers both the trusted window's
# app directory and the untrusted one.

migration_20261109_herdr_drop_team_prs_mod() {
  local home dir removed=0
  for home in "$HERDR_APP_DIR" "$HERDR_UNTRUSTED_APP_DIR"; do
    dir="$home/mods/team-prs"
    [[ -f "$dir/.workbench-install" ]] || continue
    rm -rf "$dir" || { warn "Could not remove $dir"; return 1; }
    removed=1
  done
  (( removed )) || return "$MIGRATION_NOOP"
  success "herdr-app: removed the old team-prs mod (now prs); reopen any PRs tab"
}
