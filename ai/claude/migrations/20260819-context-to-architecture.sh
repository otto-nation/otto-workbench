#!/usr/bin/env bash
set -e
# Migration: rename .claude/context.md to .claude/architecture.md across all
# projects. Idempotent — skips repos already renamed or without the file.
#
# Dated later than the rename it performs, on purpose. The first version searched
# for projects itself, found none on a machine whose repos it did not guess at,
# and recorded itself applied all the same. _prune_stale_migration_state drops the
# state entry for a filename that no longer exists, so re-dating the file is what
# gives the corrected version a run on the machines the original passed over.

migration_20260819_context_to_architecture() {
  local migrated=0
  local project_dir old new
  local -a projects=()

  # The repos to visit come from the project registry (lib/projects.sh). What
  # this replaced was a `find -maxdepth 5` over four guessed-at git roots: a repo
  # cloned anywhere else was never seen, and a bare-repo layout sits exactly at
  # that depth limit, so one directory deeper and the migration reported
  # "nothing to migrate".
  while IFS= read -r project_dir; do
    projects+=("$project_dir")
  done < <(project_registered)

  for project_dir in "${projects[@]}"; do
    old="$project_dir/.claude/context.md"
    new="$project_dir/.claude/architecture.md"

    [[ -f "$old" ]] || continue
    [[ -f "$new" ]] && continue

    mv "$old" "$new"
    migrated=$((migrated + 1))
  done

  if [[ $migrated -gt 0 ]]; then
    success "Renamed .claude/context.md → architecture.md in $migrated project(s)"
  elif [[ ${#projects[@]} -eq 0 ]]; then
    success "No projects registered yet — nothing to migrate"
  else
    success "No .claude/context.md files to migrate"
  fi
}
