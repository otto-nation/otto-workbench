#!/usr/bin/env bash
set -e
# Migration: rename .claude/context.md to .claude/architecture.md across all
# projects. Idempotent — skips repos already renamed or without the file.

migration_20260629_context_to_architecture() {
  local migrated=0
  local project_dir old new

  # The repos to visit come from the project registry (lib/projects.sh), which
  # run_all_migrations seeds before the framework starts. What this replaced was
  # a `find -maxdepth 5` over four guessed-at git roots: a repo cloned anywhere
  # else was never seen, and a bare-repo layout sits exactly at that depth
  # limit, so one directory deeper and the migration reported "nothing to
  # migrate" and recorded itself as applied (#780).
  while IFS= read -r project_dir; do
    old="$project_dir/.claude/context.md"
    new="$project_dir/.claude/architecture.md"

    [[ -f "$old" ]] || continue
    [[ -f "$new" ]] && continue

    mv "$old" "$new"
    migrated=$((migrated + 1))
  done < <(project_registered)

  if [[ $migrated -gt 0 ]]; then
    success "Renamed .claude/context.md → architecture.md in $migrated project(s)"
  else
    success "No .claude/context.md files to migrate"
  fi
}
