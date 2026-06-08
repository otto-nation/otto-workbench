#!/usr/bin/env bats
# Validates Claude Code settings.json template and registry-derived permissions.
# The template contains static permissions (shell builtins, filesystem ops).
# Tool permissions (gh, go, etc.) are derived from registry allow fields.

setup() {
  load 'test_helper'
  common_setup
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
  BREW_REGISTRY="$REPO_ROOT/brew/registry.yml"

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  common_teardown
}

# ── Template structure ───────────────────────────────────────────────────────

@test "settings.json is valid JSON" {
  run jq empty "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "settings.json has a permissions.allow array" {
  run jq -e '.permissions.allow | type == "array"' "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "settings.json has a permissions.deny array" {
  run jq -e '.permissions.deny | type == "array"' "$SETTINGS"
  [ "$status" -eq 0 ]
}

# ── Template does not contain registry-derived entries ───────────────────────

@test "template does not hardcode tool permissions that come from registries" {
  local count
  count=$(jq '[.permissions.allow[] | select(
    startswith("Bash(gh ") or
    . == "Bash(task:*)" or
    . == "Bash(go:*)" or
    . == "Bash(wt:*)" or
    . == "Bash(shellcheck:*)" or
    . == "Bash(jq:*)" or
    . == "Bash(yq:*)"
  )] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

# ── gh allow-list via registry ───────────────────────────────────────────────

@test "gh registry entry does not contain broad Bash(gh:*) wildcard" {
  local -a perms=()
  collect_registry_permissions perms "$REPO_ROOT"
  for p in "${perms[@]}"; do
    [[ "$p" != "Bash(gh:*)" ]] || { echo "broad gh wildcard found"; return 1; }
  done
}

@test "gh registry allow includes gh pr operations" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(. == "Bash(gh pr:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry allow includes gh issue operations" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(. == "Bash(gh issue:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry allow includes gh run operations" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(. == "Bash(gh run:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry allow includes gh auth status (read-only check)" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(. == "Bash(gh auth status:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry allow includes gh api for review comment workflows" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(. == "Bash(gh api:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry allow does not permit gh secret management" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(test("gh secret"))' "$BREW_REGISTRY"
  [ "$status" -ne 0 ]
}

@test "gh registry allow does not permit gh auth login or token" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(test("gh auth (login|logout|token|refresh)"))' "$BREW_REGISTRY"
  [ "$status" -ne 0 ]
}

@test "gh registry allow does not permit destructive gh repo operations" {
  run yq -e '.tools[] | select(.name == "gh") | .allow[] | select(test("gh repo (delete|edit|rename|transfer)"))' "$BREW_REGISTRY"
  [ "$status" -ne 0 ]
}

# ── git deny list ─────────────────────────────────────────────────────────────

@test "deny list blocks git push --force" {
  run jq -e '[.permissions.deny[] | select(startswith("Bash(git push --force"))] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "deny list blocks git reset" {
  run jq -e '[.permissions.deny[] | select(startswith("Bash(git reset"))] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
}

# ── sync-settings.jq integrity ───────────────────────────────────────────────

@test "sync-settings.jq file exists" {
  [ -f "$REPO_ROOT/ai/claude/sync-settings.jq" ]
}

@test "sync-settings.jq is valid jq syntax" {
  run jq -n -f "$REPO_ROOT/ai/claude/sync-settings.jq" \
    --argjson t '{"permissions":{"allow":[],"deny":[]}}' \
    --argjson e '{}'
  [ "$status" -eq 0 ]
}

# ── additionalDirectories merge ─────────────────────────────────────────────

_run_sync() {
  jq -n --argjson t "$1" --argjson e "$2" -f "$REPO_ROOT/ai/claude/sync-settings.jq"
}

@test "additionalDirectories: fresh install writes template dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/home/.claude","/home/.config/wb"]},"hooks":{}}' \
    '{}')
  local dirs
  dirs=$(jq -c '.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/home/.claude","/home/.config/wb"]' ]
}

@test "additionalDirectories: tracked in _workbench" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/a","/b"]},"hooks":{}}' \
    '{}')
  local wb_dirs
  wb_dirs=$(jq -c '._workbench.permissions.additionalDirectories' <<< "$result")
  [ "$wb_dirs" = '["/a","/b"]' ]
}

@test "additionalDirectories: user-added dirs are preserved" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/user-custom"]},"_workbench":{"permissions":{"additionalDirectories":["/managed"]}}}')
  local dirs
  dirs=$(jq -c '.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/managed","/user-custom"]' ]
}

@test "additionalDirectories: removed managed dir is dropped" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/keep"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/keep","/old-managed"]},"_workbench":{"permissions":{"additionalDirectories":["/keep","/old-managed"]}}}')
  local dirs
  dirs=$(jq -c '.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/keep"]' ]
}

@test "additionalDirectories: new managed dir is added alongside user dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed","/new-managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/user-custom"]},"_workbench":{"permissions":{"additionalDirectories":["/managed"]}}}')
  local dirs
  dirs=$(jq -c '.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/managed","/new-managed","/user-custom"]' ]
}

@test "additionalDirectories: no duplicates on first upgrade from untracked" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/a","/b"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/a"]},"_workbench":{"permissions":{}}}')
  local count
  count=$(jq '[.permissions.additionalDirectories[] | select(. == "/a")] | length' <<< "$result")
  [ "$count" -eq 1 ]
}

@test "additionalDirectories: empty template produces empty array" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[]},"hooks":{}}' \
    '{}')
  local dirs
  dirs=$(jq -c '.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '[]' ]
}

@test "additionalDirectories: _workbench does not leak user dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/secret"]},"_workbench":{"permissions":{"additionalDirectories":["/managed"]}}}')
  local wb_dirs
  wb_dirs=$(jq -c '._workbench.permissions.additionalDirectories' <<< "$result")
  [ "$wb_dirs" = '["/managed"]' ]
}
