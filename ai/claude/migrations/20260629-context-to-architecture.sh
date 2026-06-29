#!/usr/bin/env bash
set -e
# Migration: rename .claude/context.md to .claude/architecture.md across all
# projects. Idempotent — skips repos already renamed or without the file.

migration_20260629_context_to_architecture() {
  local git_roots=("$HOME/git" "$HOME/src" "$HOME/projects" "$HOME/code")
  local migrated=0
  local root project_dir old new

  for root in "${git_roots[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r claude_dir; do
      project_dir="$(dirname "$claude_dir")"
      old="$project_dir/.claude/context.md"
      new="$project_dir/.claude/architecture.md"

      [[ -f "$old" ]] || continue
      [[ -f "$new" ]] && continue

      mv "$old" "$new"
      migrated=$((migrated + 1))
    done < <(find "$root" -maxdepth 5 -type d -name ".claude" 2>/dev/null)
  done

  if [[ $migrated -gt 0 ]]; then
    success "Renamed .claude/context.md → architecture.md in $migrated project(s)"
  else
    success "No .claude/context.md files to migrate"
  fi
}
