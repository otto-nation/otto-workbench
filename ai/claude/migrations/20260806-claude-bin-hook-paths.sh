#!/usr/bin/env bash
set -e
# Migration: repoint hook and statusline commands in ~/.claude/settings.json
# from the never-created ~/.claude/bin/ to ~/.local/bin/, where ai/claude/bin
# scripts are actually installed.
#
# Managed hooks are replaced by the template on every sync, but statusLine is a
# top-level key that sync-settings.jq only adds when absent — so an existing
# settings file keeps the broken statusline path forever without this.
# Idempotent — no-op once no ~/.claude/bin/ reference remains.

# The settings file stores "$HOME/..." as literal text for Claude Code to
# expand at hook time, so the patterns below must not expand here.
# shellcheck disable=SC2016
migration_20260806_claude_bin_hook_paths() {
    local settings="$HOME/.claude/settings.json"

    if [[ ! -f "$settings" ]]; then
        success "No Claude settings to migrate"
        return 0
    fi

    if ! grep -q '\$HOME/\.claude/bin/' "$settings"; then
        success "Claude script paths already point at ~/.local/bin"
        return 0
    fi

    # Seed the temp file with cp -p so it inherits the original mode rather
    # than the umask default — the migration must not silently loosen a
    # restricted settings file. The redirect below truncates without changing
    # the mode, and cp -p is portable where the stat mode flags are not.
    local tmp="$settings.migrate.$$"
    cp -p "$settings" "$tmp"
    sed 's|\$HOME/\.claude/bin/|$HOME/.local/bin/|g' "$settings" > "$tmp"

    if ! jq empty "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        err "Rewriting Claude script paths produced invalid JSON — left $settings untouched"
        return 1
    fi

    mv "$tmp" "$settings"
    success "Repointed Claude script paths from ~/.claude/bin to ~/.local/bin"
}

migration_20260806_claude_bin_hook_paths
