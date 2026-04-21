#!/usr/bin/env bash
# Migration: clean up homegrown worktree system after switching to Worktrunk.
#
# 1. Removes .worktrees from global gitignore (Worktrunk uses sibling dirs)
# 2. Warns about any existing .worktrees/ directories that need manual cleanup

migration_20260421_worktrunk_migration() {
  local ignore_file="${HOME}/.config/git/ignore"

  # Remove .worktrees entry from global gitignore
  if [[ -f "$ignore_file" ]] && grep -qxF ".worktrees" "$ignore_file" 2>/dev/null; then
    local tmp
    tmp=$(mktemp)
    grep -vxF ".worktrees" "$ignore_file" > "$tmp" && mv "$tmp" "$ignore_file"
    info "Removed .worktrees from global gitignore"
  fi

  # Warn about any existing .worktrees/ directories
  local found=false
  local dir
  for dir in "$HOME"/git/*/*/.worktrees "$HOME"/git/*/.worktrees; do
    if [[ -d "$dir" ]]; then
      if [[ "$found" == false ]]; then
        warn "Found .worktrees/ directories from the old worktree system:"
        found=true
      fi
      echo "  ${dir}"
    fi
  done

  if [[ "$found" == true ]]; then
    info "These are now unmanaged. Clean up with: git worktree remove <path>"
    info "Worktrunk uses sibling directories instead: wt switch -c <name>"
  fi
}
