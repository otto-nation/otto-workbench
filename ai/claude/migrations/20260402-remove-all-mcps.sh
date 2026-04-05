#!/usr/bin/env bash
# Migration: remove all MCP server registrations from ~/.claude.json.
# Serena, Context7, and Sequential Thinking were removed from the workbench.
# step_claude_mcps() only adds — it never prunes removed manifests.
# Idempotent — no-op if the keys do not exist.

migration_20260402_remove_all_mcps() {
  [[ -f "$CLAUDE_CONFIG_FILE" ]] || return 0

  local names=("serena" "context7" "sequential-thinking")
  local name removed=false

  for name in "${names[@]}"; do
    if jq -e ".mcpServers | has(\"$name\")" "$CLAUDE_CONFIG_FILE" > /dev/null 2>&1; then
      local tmp
      tmp=$(mktemp)
      jq --arg n "$name" 'del(.mcpServers[$n])' "$CLAUDE_CONFIG_FILE" > "$tmp" \
        && mv "$tmp" "$CLAUDE_CONFIG_FILE"
      success "Removed MCP registration: $name"
      removed=true
    fi
  done

  if [[ "$removed" == false ]]; then
    success "No stale MCP registrations found"
  fi
}
