#!/usr/bin/env bats
# Validates the Claude Code settings.json allow/deny lists.
# Ensures gh permissions follow the allow-list model (no broad wildcard,
# no gh api exposure) and that known destructive git operations are denied.

setup() {
  REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
}

# ── Structure ─────────────────────────────────────────────────────────────────

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

# ── gh allow-list: no bare wildcard ──────────────────────────────────────────

@test "allow list does not contain the broad Bash(gh:*) wildcard" {
  local count
  count=$(jq '[.permissions.allow[] | select(. == "Bash(gh:*)")] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

@test "allow list does not permit gh api (raw REST/GraphQL)" {
  local count
  count=$(jq '[.permissions.allow[] | select(test("Bash\\(gh api"))] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

@test "allow list does not permit gh secret management" {
  local count
  count=$(jq '[.permissions.allow[] | select(test("Bash\\(gh secret"))] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

@test "allow list does not permit gh auth login or token" {
  local count
  count=$(jq '[.permissions.allow[] | select(test("Bash\\(gh auth (login|logout|token|refresh)"))] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

@test "allow list does not permit destructive gh repo operations" {
  local count
  count=$(jq '[.permissions.allow[] | select(test("Bash\\(gh repo (delete|edit|rename|transfer)"))] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

# ── gh allow-list: safe operations present ────────────────────────────────────

@test "allow list includes gh pr operations" {
  run jq -e '[.permissions.allow[] | select(startswith("Bash(gh pr:"))] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "allow list includes gh issue operations" {
  run jq -e '[.permissions.allow[] | select(startswith("Bash(gh issue:"))] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "allow list includes gh run operations" {
  run jq -e '[.permissions.allow[] | select(startswith("Bash(gh run:"))] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
}

@test "allow list includes gh auth status (read-only check)" {
  run jq -e '[.permissions.allow[] | select(. == "Bash(gh auth status:*)")] | length > 0' "$SETTINGS"
  [ "$status" -eq 0 ]
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
