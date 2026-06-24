#!/usr/bin/env bats
# Validates Claude Code settings.json template and registry-derived permissions.
# The template contains static permissions (shell builtins, filesystem ops).
# Tool permissions (gh, go, etc.) are derived from registry permission fields.

setup_file() {
  load 'test_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  # shellcheck source=/dev/null
  source "$repo_root/lib/registries.sh"

  # Collect registry permissions once for all tests
  local -a perms=()
  collect_registry_permissions perms "$repo_root"
  printf '%s\n' "${perms[@]}" > "$BATS_FILE_TMPDIR/registry_perms.list"
}

setup() {
  load 'test_helper'
  common_setup
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
  BREW_REGISTRY="$REPO_ROOT/brew/registry.yml"
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

# ── Registry permissions are auto-managed ────────────────────────────────────

@test "registry-derived permissions are tracked in _generated_permissions" {
  local -a registry_perms=()
  mapfile -t registry_perms < "$BATS_FILE_TMPDIR/registry_perms.list"
  [ "${#registry_perms[@]}" -gt 0 ]
  for perm in "${registry_perms[@]}"; do
    run jq -e --arg p "$perm" '._generated_permissions | index($p) != null' "$SETTINGS"
    [ "$status" -eq 0 ] || { echo "missing from _generated_permissions: $perm"; return 1; }
  done
}

@test "_generated_permissions entries are all in permissions.allow" {
  local count
  count=$(jq '.permissions.allow as $allow |
    [._generated_permissions[] | select(. as $p | $allow | index($p) == null)] | length' "$SETTINGS")
  [ "$count" -eq 0 ]
}

# ── gh permission-list via registry ───────────────────────────────────────────────

@test "gh registry entry does not contain broad Bash(gh:*) wildcard" {
  local -a perms=()
  mapfile -t perms < "$BATS_FILE_TMPDIR/registry_perms.list"
  for p in "${perms[@]}"; do
    [[ "$p" != "Bash(gh:*)" ]] || { echo "broad gh wildcard found"; return 1; }
  done
}

@test "gh registry permission includes gh pr operations" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(. == "Bash(gh pr:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry permission includes gh issue operations" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(. == "Bash(gh issue:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry permission includes gh run operations" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(. == "Bash(gh run:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry permission includes gh auth status (read-only check)" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(. == "Bash(gh auth status:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry permission includes gh api for review comment workflows" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(. == "Bash(gh api:*)")' "$BREW_REGISTRY"
  [ "$status" -eq 0 ]
}

@test "gh registry permission does not permit gh secret management" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(test("gh secret"))' "$BREW_REGISTRY"
  [ "$status" -ne 0 ]
}

@test "gh registry permission does not permit gh auth login or token" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(test("gh auth (login|logout|token|refresh)"))' "$BREW_REGISTRY"
  [ "$status" -ne 0 ]
}

@test "gh registry permission does not permit destructive gh repo operations" {
  run yq -e '.tools[] | select(.name == "gh") | .permission[] | select(test("gh repo (delete|edit|rename|transfer)"))' "$BREW_REGISTRY"
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

# ── Hook behavior ────────────────────────────────────────────────────────────

# Extracts and evaluates a hook command from settings.json.
# The hook reads tool_input from stdin (JSON), so we pipe a mock payload.
_run_hook() {
  local hook_cmd=$1 tool_input=$2
  echo "$tool_input" | bash -c "$hook_cmd" 2>&1
}

_get_brace_hook() {
  jq -r '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[] |
    select(.command | test("Brace expansion")) | .command' "$SETTINGS"
}

_get_branch_hook() {
  jq -r '.hooks.PreToolUse[] | select(.matcher == "Edit|Write") | .hooks[0].command' "$SETTINGS"
}

@test "brace hook: blocks real brace expansion" {
  local hook
  hook=$(_get_brace_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"cp file.{txt,bak}"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Brace expansion"* ]]
}

@test "brace hook: allows heredoc with braces in body" {
  local hook cmd
  hook=$(_get_brace_hook)
  cmd=$(printf 'python3 << '\''PYEOF'\''\nd = {"a": 1, "b": 2}\nPYEOF')
  run _run_hook "$hook" "{\"tool_input\":{\"command\":$(jq -Rsa '.' <<< "$cmd")}}"
  [ "$status" -eq 0 ]
}

@test "brace hook: allows python -c with dict in double quotes" {
  local hook
  hook=$(_get_brace_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"python3 -c \"d = {\\\"a\\\": 1, \\\"b\\\": 2}\""}}'
  [ "$status" -eq 0 ]
}

@test "brace hook: allows jq with braces in single quotes" {
  local hook
  hook=$(_get_brace_hook)
  run _run_hook "$hook" "{\"tool_input\":{\"command\":\"jq '.items[] | {name, value}' file.json\"}}"
  [ "$status" -eq 0 ]
}

_init_test_repo() {
  local dir=$1 branch=${2:-main}
  git -C "$dir" init -b "$branch" --quiet
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
}

@test "branch hook: blocks tracked file on main" {
  local hook tmpdir
  hook=$(_get_branch_hook)
  tmpdir=$(mktemp -d)
  _init_test_repo "$tmpdir"
  touch "$tmpdir/tracked.txt"
  git -C "$tmpdir" add tracked.txt
  git -C "$tmpdir" commit -m "init" --quiet
  run _run_hook "$hook" "{\"tool_input\":{\"file_path\":\"$tmpdir/tracked.txt\"}}"
  rm -rf "$tmpdir"
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "branch hook: allows gitignored file on main" {
  local hook tmpdir
  hook=$(_get_branch_hook)
  tmpdir=$(mktemp -d)
  _init_test_repo "$tmpdir"
  echo "ignore/" > "$tmpdir/.gitignore"
  git -C "$tmpdir" add .gitignore
  git -C "$tmpdir" commit -m "init" --quiet
  mkdir -p "$tmpdir/ignore/specs"
  run _run_hook "$hook" "{\"tool_input\":{\"file_path\":\"$tmpdir/ignore/specs/test.md\"}}"
  rm -rf "$tmpdir"
  [ "$status" -eq 0 ]
}

@test "branch hook: allows any file on feature branch" {
  local hook tmpdir
  hook=$(_get_branch_hook)
  tmpdir=$(mktemp -d)
  _init_test_repo "$tmpdir"
  touch "$tmpdir/file.txt"
  git -C "$tmpdir" add file.txt
  git -C "$tmpdir" commit -m "init" --quiet
  git -C "$tmpdir" checkout -b feature --quiet
  run _run_hook "$hook" "{\"tool_input\":{\"file_path\":\"$tmpdir/file.txt\"}}"
  rm -rf "$tmpdir"
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
