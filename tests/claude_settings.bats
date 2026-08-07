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

# The VAR=, compound-cd, brace-expansion, and function-definition checks share a
# single hook — it strips quoted spans off the first line once, then runs each
# regex. Selecting by a phrase from any one message returns that same command.
_get_bash_hook() {
  jq -r --arg needle "$1" '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[] |
    select(.command | contains($needle)) | .command' "$SETTINGS"
}

_get_brace_hook() {
  _get_bash_hook "Brace expansion"
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

# ── gh pr create block ──────────────────────────────────────────────────────

_get_pr_create_hook() {
  jq -r '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[] |
    select(.command | test("gh pr create")) | .command' "$SETTINGS"
}

@test "pr create hook: blocks gh pr create" {
  local hook
  hook=$(_get_pr_create_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"gh pr create"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: blocks gh pr create --draft" {
  local hook
  hook=$(_get_pr_create_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"gh pr create --draft --title \"fix: thing\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: allows gh pr list" {
  local hook
  hook=$(_get_pr_create_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"gh pr list --state open"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh pr view" {
  local hook
  hook=$(_get_pr_create_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"gh pr view 42 --json state"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh api" {
  local hook
  hook=$(_get_pr_create_hook)
  run _run_hook "$hook" '{"tool_input":{"command":"gh api repos/owner/repo/pulls"}}'
  [ "$status" -eq 0 ]
}

# ── statement-anchored Bash guardrails ──────────────────────────────────────
# These checks match at the start of any statement, not just the start of the
# command — a leading no-op token must not be a way around them. They scope to
# the first line so a heredoc body being written to a file is not scanned as if
# it were the command itself.
#
# ceiling: the hook strips quoted spans with two sed passes, which mis-handles
# escaped quotes and embedded apostrophes, in both directions. An unpaired
# apostrophe re-pairs with a later quote, so `echo it's; cd /x && ls 'q'` strips
# the real cd away and is not blocked; an escaped quote ends a span early, so
# `echo "say \"{a,b}\" now"` strips to `echo {a,b}\` and is blocked as brace
# expansion. Both outcomes cost at most one permission prompt — these guardrails
# steer command style, they are not a security boundary. Upgrade to a real
# tokenizer if either misfire shows up on a command worth running.

@test "the four first-line checks live in exactly one hook" {
  local needle count
  for needle in "function_definition" "VAR=value" "Compound cd" "Brace expansion"; do
    count=$(_get_bash_hook "$needle" | wc -l | tr -d ' ')
    [ "$count" -eq 1 ] || {
      echo "'$needle' matched $count hooks — the quote-stripping preamble was duplicated"
      return 1
    }
  done
}

@test "funcdef hook: blocks a cd() no-op stub wrapping a grep" {
  local hook
  hook=$(_get_bash_hook "function_definition")
  run _run_hook "$hook" '{"tool_input":{"command":"cd() { :; }; W=/tmp/x; grep -rn foo \"$W/tests/\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"function_definition"* ]]
}

@test "funcdef hook: blocks the function keyword form" {
  local hook
  hook=$(_get_bash_hook "function_definition")
  run _run_hook "$hook" '{"tool_input":{"command":"function run { echo hi; }; run"}}'
  [ "$status" -eq 2 ]
}

@test "funcdef hook: allows a plain grep with parens inside quotes" {
  local hook
  hook=$(_get_bash_hook "function_definition")
  run _run_hook "$hook" "{\"tool_input\":{\"command\":\"grep -rnE '(worktree list|worktree_list)' /tmp/x/tests/ | head -40\"}}"
  [ "$status" -eq 0 ]
}

@test "var hook: blocks a VAR=value prefix at the start" {
  local hook
  hook=$(_get_bash_hook "VAR=value")
  run _run_hook "$hook" '{"tool_input":{"command":"W=/tmp/x grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: blocks a VAR=value assignment after a leading token" {
  local hook
  hook=$(_get_bash_hook "VAR=value")
  run _run_hook "$hook" '{"tool_input":{"command":"true; W=/tmp/x; grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: allows uppercase flag values mid-command" {
  local hook
  hook=$(_get_bash_hook "VAR=value")
  run _run_hook "$hook" '{"tool_input":{"command":"docker run -e FOO=bar alpine"}}'
  [ "$status" -eq 0 ]
}

@test "cd hook: blocks a compound cd after a leading token" {
  local hook
  hook=$(_get_bash_hook "Compound cd")
  run _run_hook "$hook" '{"tool_input":{"command":"mkdir -p /tmp/x; cd /tmp/x && ls"}}'
  [ "$status" -eq 2 ]
}

@test "cd hook: allows a cd nested inside a quoted argument" {
  local hook
  hook=$(_get_bash_hook "Compound cd")
  run _run_hook "$hook" "{\"tool_input\":{\"command\":\"bash -c 'cd /tmp/x && ls'\"}}"
  [ "$status" -eq 0 ]
}

# The bodies below each put the pattern after a statement separator, which is
# what the whole-command form matched on — a body line starting with the pattern
# was never a false positive, since the regex only anchors to start-of-string.

@test "funcdef hook: allows a function definition inside a heredoc body" {
  local hook
  hook=$(_get_bash_hook "function_definition")
  run _run_hook "$hook" '{"tool_input":{"command":"cat > /tmp/x/lib.sh <<EOF\nsetup; run() { echo hi; }\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "var hook: allows an assignment inside a heredoc body" {
  local hook
  hook=$(_get_bash_hook "VAR=value")
  run _run_hook "$hook" '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\ntrue; FOO=bar\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "cd hook: allows a compound cd inside a heredoc body" {
  local hook
  hook=$(_get_bash_hook "Compound cd")
  run _run_hook "$hook" '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\nmkdir -p /tmp/y; cd /tmp/y && ls\nEOF"}}'
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

# ── script paths referenced from settings ────────────────────────────────────
# Hook and statusline commands name installed scripts by absolute path, and
# nothing resolves those paths at install time. A wrong directory therefore
# fails silently — most of these commands end in `|| true`, and the statusline
# just renders nothing — so the hook looks configured but never runs.

# Every "$HOME/..." path named by the statusline or a hook command. Includes
# data paths (log dirs) as well as scripts — callers filter by prefix.
_referenced_home_paths() {
  jq -r '[.statusLine.command] + [.hooks[][].hooks[].command] | .[]' "$SETTINGS" |
    grep -oE '[$]HOME/[^" ]*' | sort -u
}

@test "settings reference no bin dir other than LOCAL_BIN_DIR" {
  local expected path
  expected=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$expected" ]

  while read -r path; do
    case "$path" in
      "$expected"/*) continue ;;
      */bin/*)
        echo "settings.json references '$path'"
        echo "installed scripts go to $expected (LOCAL_BIN_DIR in lib/constants.sh)"
        return 1
        ;;
    esac
  done < <(_referenced_home_paths)
}

@test "every bin script referenced by settings exists in ai/claude/bin" {
  local expected path missing=()
  expected=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")

  while read -r path; do
    case "$path" in
      "$expected"/*) ;;
      *) continue ;;
    esac
    [ -f "$REPO_ROOT/ai/claude/bin/${path##*/}" ] || missing+=("${path##*/}")
  done < <(_referenced_home_paths)

  [ ${#missing[@]} -eq 0 ] || {
    echo "referenced by settings.json but absent from ai/claude/bin: ${missing[*]}"
    return 1
  }
}

@test "every skill script referenced by settings exists in ai/claude/skills" {
  local path rel missing=()
  while read -r path; do
    case "$path" in
      '$HOME/.claude/skills/'*) ;;
      *) continue ;;
    esac
    rel=${path#'$HOME/.claude/skills/'}
    [ -f "$REPO_ROOT/ai/claude/skills/$rel" ] || missing+=("$rel")
  done < <(_referenced_home_paths)

  [ ${#missing[@]} -eq 0 ] || {
    echo "referenced by settings.json but absent from ai/claude/skills: ${missing[*]}"
    return 1
  }
}
