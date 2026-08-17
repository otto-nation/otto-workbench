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

# Extracts and evaluates an inline hook command from settings.json.
# The hook reads tool_input from stdin (JSON), so we pipe a mock payload.
_run_hook() {
  local hook_cmd=$1 tool_input=$2
  echo "$tool_input" | bash -c "$hook_cmd" 2>&1
}

# Runs the Bash PreToolUse guard against a mock payload. Every Bash rule lives
# in that one script, so these tests exercise the source rather than a
# JSON-escaped copy of it.
_run_guard() {
  echo "$1" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" 2>&1
}

@test "settings delegates every Bash rule to the guard script" {
  local bin_dir cmds
  bin_dir=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$bin_dir" ]

  cmds=$(jq -r '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[].command' "$SETTINGS")
  [ "$cmds" = "bash $bin_dir/claude-bash-guard" ] || {
    echo "expected a single guard invocation, got:"
    echo "$cmds"
    return 1
  }
}

@test "guard: exits 0 on a payload with no command" {
  run _run_guard '{"tool_input":{}}'
  [ "$status" -eq 0 ]
}

@test "guard: fails open on a malformed payload" {
  run _run_guard 'not json'
  [ "$status" -eq 0 ]
}

_get_branch_hook() {
  jq -r '.hooks.PreToolUse[] | select(.matcher == "Edit|Write") | .hooks[0].command' "$SETTINGS"
}

@test "brace hook: blocks real brace expansion" {
  run _run_guard '{"tool_input":{"command":"cp file.{txt,bak}"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Brace expansion"* ]]
}

@test "brace hook: allows heredoc with braces in body" {
  local cmd
  cmd=$(printf 'python3 << '\''PYEOF'\''\nd = {"a": 1, "b": 2}\nPYEOF')
  run _run_guard "{\"tool_input\":{\"command\":$(jq -Rsa '.' <<< "$cmd")}}"
  [ "$status" -eq 0 ]
}

@test "brace hook: allows python -c with dict in double quotes" {
  run _run_guard '{"tool_input":{"command":"python3 -c \"d = {\\\"a\\\": 1, \\\"b\\\": 2}\""}}'
  [ "$status" -eq 0 ]
}

@test "brace hook: allows jq with braces in single quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"jq '.items[] | {name, value}' file.json\"}}"
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

@test "pr create hook: blocks gh pr create" {
  run _run_guard '{"tool_input":{"command":"gh pr create"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: blocks gh pr create --draft" {
  run _run_guard '{"tool_input":{"command":"gh pr create --draft --title \"fix: thing\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: allows gh pr list" {
  run _run_guard '{"tool_input":{"command":"gh pr list --state open"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh pr view" {
  run _run_guard '{"tool_input":{"command":"gh pr view 42 --json state"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh api" {
  run _run_guard '{"tool_input":{"command":"gh api repos/owner/repo/pulls"}}'
  [ "$status" -eq 0 ]
}

# ── PATH binary absolute paths ──────────────────────────────────────────────
# The allow list keys on the bare command name, so `Bash(cat:*)` never matches
# `/bin/cat` and `Bash(mise:*)` never matches `~/.local/bin/mise` — the absolute
# form prompts on every call. The rule covers every bin/ on the default PATH.
# It rides the same quote-stripped first line as the four guardrails below, so
# such a path inside a quoted argument is not mistaken for an invocation.

@test "binlocal hook: blocks an absolute path to a bin/local script" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "binlocal hook: blocks an absolute path after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "binlocal hook: names the git/ prefixed path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/git/bin/local/generate-git-rules"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'git/bin/local/generate-git-rules'"* ]]
}

@test "binlocal hook: allows the relative form" {
  run _run_guard '{"tool_input":{"command":"bin/local/validate-all"}}'
  [ "$status" -eq 0 ]
}

@test "binlocal hook: allows an absolute path inside a quoted argument" {
  run _run_guard '{"tool_input":{"command":"git commit -m \"drop; /Users/me/repo/bin/local/old\""}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: blocks /bin/cat and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/bin/cat /tmp/x/review.diff"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'cat'"* ]]
}

@test "pathbin hook: blocks /usr/bin after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /usr/bin/grep -n foo f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'grep'"* ]]
}

@test "pathbin hook: blocks /bin with no space after the separator" {
  run _run_guard '{"tool_input":{"command":"ls -la;/bin/cat f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'cat'"* ]]
}

@test "pathbin hook: blocks a ~/.local/bin path and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/bin/mise doctor"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'mise'"* ]]
  [[ "$output" == *"/Users/me/.local/bin/"* ]]
}

@test "pathbin hook: blocks a ~/.local/bin path after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /Users/me/.local/bin/rtk read f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'rtk'"* ]]
}

@test "pathbin hook: blocks a homebrew path and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/opt/homebrew/bin/gh pr view 42"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'gh'"* ]]
}

@test "pathbin hook: blocks /usr/local/bin and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/usr/local/bin/node --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: allows the bare command name" {
  run _run_guard '{"tool_input":{"command":"cat /tmp/x/review.diff"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a /bin path inside a sed expression" {
  run _run_guard "{\"tool_input\":{\"command\":\"sed -e 's|/bin/cat|x|' f\"}}"
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows absolute paths outside every PATH bin dir" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/scripts/thing"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a PATH bin path that is an argument, not the command" {
  run _run_guard '{"tool_input":{"command":"ls -la /Users/me/.local/bin/mise"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a separator-prefixed path inside a quoted argument" {
  run _run_guard '{"tool_input":{"command":"git commit -m \"fix: drop; /usr/bin/env callers\""}}'
  [ "$status" -eq 0 ]
}

# ── statement-anchored Bash guardrails ──────────────────────────────────────
# These checks match at the start of any statement, not just the start of the
# command — a leading no-op token must not be a way around them. They scope to
# the first line so a heredoc body being written to a file is not scanned as if
# it were the command itself. The quote-stripping ceiling is documented in the
# guard script itself.

@test "funcdef hook: blocks a cd() no-op stub wrapping a grep" {
  run _run_guard '{"tool_input":{"command":"cd() { :; }; W=/tmp/x; grep -rn foo \"$W/tests/\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"function_definition"* ]]
}

@test "funcdef hook: blocks the function keyword form" {
  run _run_guard '{"tool_input":{"command":"function run { echo hi; }; run"}}'
  [ "$status" -eq 2 ]
}

@test "funcdef hook: allows a plain grep with parens inside quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -rnE '(worktree list|worktree_list)' /tmp/x/tests/ | head -40\"}}"
  [ "$status" -eq 0 ]
}

@test "var hook: blocks a VAR=value prefix at the start" {
  run _run_guard '{"tool_input":{"command":"W=/tmp/x grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: blocks a VAR=value assignment after a leading token" {
  run _run_guard '{"tool_input":{"command":"true; W=/tmp/x; grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: allows uppercase flag values mid-command" {
  run _run_guard '{"tool_input":{"command":"docker run -e FOO=bar alpine"}}'
  [ "$status" -eq 0 ]
}

@test "cd hook: blocks a compound cd after a leading token" {
  run _run_guard '{"tool_input":{"command":"mkdir -p /tmp/x; cd /tmp/x && ls"}}'
  [ "$status" -eq 2 ]
}

@test "cd hook: allows a cd nested inside a quoted argument" {
  run _run_guard "{\"tool_input\":{\"command\":\"bash -c 'cd /tmp/x && ls'\"}}"
  [ "$status" -eq 0 ]
}

@test "env hook: blocks env -C" {
  run _run_guard '{"tool_input":{"command":"env -C /tmp/wt pytest tests/foo_test.py -q"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: blocks env --chdir" {
  run _run_guard '{"tool_input":{"command":"env --chdir=/tmp/wt bats tests/"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: blocks env -C after a pipe" {
  run _run_guard '{"tool_input":{"command":"echo hi | env -C /tmp/wt cat"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: allows env without a directory flag" {
  run _run_guard '{"tool_input":{"command":"env | grep PATH"}}'
  [ "$status" -eq 0 ]
}

@test "env hook: blocks env -C behind another flag" {
  run _run_guard '{"tool_input":{"command":"env -i -C /tmp/wt pytest tests/"}}'
  [ "$status" -eq 2 ]
}

@test "env hook: blocks env -C with an attached directory" {
  run _run_guard '{"tool_input":{"command":"env -C/tmp/wt ls"}}'
  [ "$status" -eq 2 ]
}

@test "env hook: allows an env var assignment passed to env" {
  run _run_guard '{"tool_input":{"command":"env FOO=bar printenv FOO"}}'
  [ "$status" -eq 0 ]
}

# ── Guard ↔ rules contract ───────────────────────────────────────────────────
# A block message must name the alternative and cite the rules section holding
# the rest. A rule blocking Claude with no documented alternative is worse than
# the prompt it prevents, so both halves of the citation are tested: that every
# block call carries one, and that every one it carries resolves to a heading.

@test "guard: every block call cites a rules section" {
  local guard="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  local uncited
  uncited=$(grep -n '^  block ' "$guard" | grep -v 'See [a-z-]*\.md §' || true)
  [ -z "$uncited" ] || {
    echo "block call(s) with no 'See <doc>.md § <Section>' citation:"
    echo "$uncited"
    return 1
  }
}

@test "guard: every cited rules section exists" {
  local guard="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  local doc section
  # Headings are compared with backticks stripped: the guard's messages are
  # plain text, so `## Avoid \`env -C\`` is cited as `§ Avoid env -C`.
  while IFS='|' read -r doc section; do
    [ -f "$REPO_ROOT/ai/guidelines/rules/$doc" ] || {
      echo "guard cites a rules file that does not exist: $doc"
      return 1
    }
    sed 's/`//g' "$REPO_ROOT/ai/guidelines/rules/$doc" | grep -qxF "## $section" || {
      echo "guard cites '$doc § $section', which has no matching heading"
      return 1
    }
  done < <(grep -oE 'See [a-z-]+\.md § [^.]+\.' "$guard" |
    sed -E 's/^See ([a-z-]+\.md) § (.*)\.$/\1|\2/' | sort -u)
}

# The bodies below each put the pattern after a statement separator, which is
# what the whole-command form matched on — a body line starting with the pattern
# was never a false positive, since the regex only anchors to start-of-string.

@test "funcdef hook: allows a function definition inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/lib.sh <<EOF\nsetup; run() { echo hi; }\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "var hook: allows an assignment inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\ntrue; FOO=bar\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "binlocal hook: allows an absolute bin/local path inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\ntrue; /Users/me/repo/bin/local/validate-all\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "cd hook: allows a compound cd inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\nmkdir -p /tmp/y; cd /tmp/y && ls\nEOF"}}'
  [ "$status" -eq 0 ]
}

# ── whole-command Bash guardrails ───────────────────────────────────────────
# These scan the entire command rather than the quote-stripped first line: the
# analyzer flags them wherever they appear, including inside a quoted argument.

@test "exec hook: blocks find -exec" {
  run _run_guard '{"tool_input":{"command":"find . -name x -exec grep foo {} ;"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"-print0"* ]]
}

@test "subst hook: blocks command substitution" {
  run _run_guard '{"tool_input":{"command":"ls $(git rev-parse --show-toplevel)"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Run the inner command first"* ]]
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
