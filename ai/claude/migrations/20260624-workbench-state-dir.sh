#!/usr/bin/env bash
set -e
# Migration: relocate PR and CI state from ignore/pr/ and ignore/ci-failures/
# to .workbench/. Idempotent — no-op if already migrated or nothing to migrate.

migration_20260624_workbench_state_dir() {
    local migrated=0

    # Migrate worktree state found in a given root directory
    _migrate_worktree_state() {
        local root="$1"
        local old_pr="$root/ignore/pr/state.json"
        local old_ci="$root/ignore/ci-failures/state.json"
        local new_dir="$root/.workbench"
        local new_file="$new_dir/state.json"

        # Skip if already migrated
        if [[ -f "$new_file" ]]; then
            return 0
        fi

        # Skip if nothing to migrate
        if [[ ! -f "$old_pr" ]] && [[ ! -f "$old_ci" ]]; then
            return 0
        fi

        mkdir -p "$new_dir"

        # If pr state exists, use it as the base (it's the unified format)
        if [[ -f "$old_pr" ]]; then
            cp "$old_pr" "$new_file"
        fi

        # Clean up old directories
        rm -f "$old_pr" "$old_ci"
        rmdir "$root/ignore/pr" 2>/dev/null || true
        rmdir "$root/ignore/ci-failures" 2>/dev/null || true
        rmdir "$root/ignore" 2>/dev/null || true

        migrated=1
    }

    # Migrate the current worktree
    _migrate_worktree_state "$WORKBENCH_DIR"

    if [[ $migrated -gt 0 ]]; then
        success "Migrated worktree state from ignore/ to .workbench/"
    else
        success "No worktree state to migrate"
    fi
}

migration_20260624_workbench_state_dir
