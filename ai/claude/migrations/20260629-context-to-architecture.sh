#!/usr/bin/env bash
set -e
# Migration: rename .claude/context.md to .claude/architecture.md across all
# projects. Idempotent — skips repos already renamed or without the file.

migration_20260629_context_to_architecture() {
  local git_roots=("$HOME/git" "$HOME/src" "$HOME/projects" "$HOME/code")
  local migrated=0

  _rename_if_needed() {
    local dir="$1"
    local old="$dir/.claude/context.md"
    local new="$dir/.claude/architecture.md"

    [[ -f "$old" ]] || return 0
    [[ -f "$new" ]] && return 0

    mv "$old" "$new"
    migrated=$((migrated + 1))
  }

  # Scan git worktrees under known roots
  local root
  for root in "${git_roots[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r claude_dir; do
      local project_dir
      project_dir="$(dirname "$claude_dir")"
      _rename_if_needed "$project_dir"
    done < <(find "$root" -maxdepth 5 -type d -name ".claude" 2>/dev/null)
  done

  if [[ $migrated -gt 0 ]]; then
    success "Renamed .claude/context.md → architecture.md in $migrated project(s)"
  else
    success "No .claude/context.md files to migrate"
  fi
}

migration_20260629_context_to_architecture
