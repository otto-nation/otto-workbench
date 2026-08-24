#!/usr/bin/env bash
# Migration: remove all MCP server registrations from ~/.claude.json.
# Serena, Context7, and Sequential Thinking were removed from the workbench.
# step_claude_mcps() only adds — it never prunes removed manifests.
# Idempotent — no-op if the keys do not exist.

migration_20260402_remove_all_mcps() {
  # NOOP rather than deferred, even though ~/.claude.json can appear later.
  # This drains keys, and a ~/.claude.json written after this ran is a fresh
  # install whose MCP entries the operator chose — draining those is the exact
  # undo that _forget_adoption_sensitive_migrations refuses to perform for a
  # removal migration. A machine with no file has nothing stale to remove, and
  # that will not change.
  [[ -f "$CLAUDE_CONFIG_FILE" ]] || return "$MIGRATION_NOOP"

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
    return "$MIGRATION_NOOP"
  fi
}
