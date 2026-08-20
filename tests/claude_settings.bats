#!/usr/bin/env bats
# Validates Claude Code settings.json template and registry-derived permissions.
# The template contains handwritten permissions only (shell builtins, filesystem
# ops). Tool permissions (gh, go, etc.) are derived from registry permission
# fields by step_claude_settings and only ever land in ~/.claude/settings.json,
# so the synced sandbox below — not the template — is where they are asserted.
#
# Grants for this repo's own scripts are neither: they live in the tracked
# .claude/settings.json, which applies to this repo alone and travels with every
# worktree. Those are asserted against that file.

# _sync_settings_into FAKE_HOME REPO_ROOT — runs step_claude_settings against a
# sandbox HOME. constants.sh derives every path from HOME at source time, so
# presetting it keeps the real ~/.claude untouched.
_sync_settings_into() {
  local fake_home="$1" repo_root="$2"
  mkdir -p "$fake_home"
  HOME="$fake_home"
  export WORKBENCH_DIR="$repo_root"
  export WORKBENCH_STABLE_DIR="$repo_root"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$repo_root/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$repo_root/ai/claude/steps.sh"
  step_claude_settings >/dev/null
}

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

  _sync_settings_into "$BATS_FILE_TMPDIR/home" "$repo_root"
}

setup() {
  load 'test_helper'
  common_setup
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
  PROJECT_SETTINGS="$REPO_ROOT/.claude/settings.json"
  SYNCED="$BATS_FILE_TMPDIR/home/.claude/settings.json"
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

# ── Tracked project allowlist ────────────────────────────────────────────────
# Grants for this repo's own scripts live in a tracked .claude/settings.json so
# every worktree inherits them and a reviewer sees them. The machine-level
# template must not carry them: a rule there applies to every repo on the
# machine, including ones that were only just cloned.

# project_granted_dirs — the directory each tracked project grant covers, one per
# line. The tracked file is the single owner of that list; every check below that
# needs it reads it from here rather than restating it.
project_granted_dirs() {
  jq -r '.permissions.allow[]' "$PROJECT_SETTINGS" | sed 's/^Bash(//; s|/\*)$||'
}

@test "the tracked project settings file is committed, not ignored" {
  run git -C "$REPO_ROOT" ls-files --error-unmatch .claude/settings.json
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "the tracked project settings file is valid JSON" {
  run jq empty "$PROJECT_SETTINGS"
  [ "$status" -eq 0 ]
}

# Every rule is a wildcard over one directory the repo ships, which is the whole
# grant this file is allowed to make. It is what keeps a `Bash(gh *)` — the shape
# that accumulated in the untracked file this replaces — from landing here.
@test "every tracked project grant is a directory this repo ships" {
  local rule dir
  while read -r rule; do
    [[ "$rule" == Bash\(*/\*\) ]] || { echo "not a Bash directory rule: $rule"; return 1; }
    dir=${rule#Bash(}
    dir=${dir%/*)}
    [ -d "$REPO_ROOT/$dir" ] || { echo "grants a directory that does not exist: $dir"; return 1; }
  done < <(jq -r '.permissions.allow[]' "$PROJECT_SETTINGS")
}

@test "the tracked project settings file grants every repo bin directory" {
  local dir
  for dir in bin git/bin ai/claude/bin; do
    run jq -e --arg r "Bash($dir/*)" '.permissions.allow | index($r) != null' "$PROJECT_SETTINGS"
    [ "$status" -eq 0 ] || { echo "no grant for $dir/"; return 1; }
  done
}

# The two `local` names are the grants this move removed from the template. They
# are named outright because nothing tracks them any more, so only a test keeps
# them from being restored by hand.
@test "the machine-level template carries no repo-scoped script grant" {
  local dir
  for dir in $(project_granted_dirs) bin/local git/bin/local; do
    run jq -e --arg r "Bash($dir/*)" '.permissions.allow | index($r) != null' "$SETTINGS"
    [ "$status" -ne 0 ] || { echo "repo-scoped grant in the machine template: Bash($dir/*)"; return 1; }
  done
}

# A blanket directory wildcard is the right default for scripts a checkout already
# trusts, but two of them reach credentials — one reads secrets out of AWS Secrets
# Manager, the other rewrites GCP application-default credentials — and neither
# should run without the human seeing it. `ask` outranks `allow`, so the carve-out
# restores the prompt without taking the script away.
@test "the credential-facing scripts are held back to a prompt" {
  local script
  for script in bin/get-secret bin/gcloud-reauth; do
    [ -f "$REPO_ROOT/$script" ] || { echo "no such script: $script"; return 1; }
    run jq -e --arg r "Bash($script:*)" '.permissions.ask | index($r) != null' "$PROJECT_SETTINGS"
    [ "$status" -eq 0 ] || { echo "$script is covered by the wildcard with no ask rule"; return 1; }
  done
}

# claude-bash-guard steers a ./-prefixed or absolute invocation back to the form
# the allow list keys on, so it has to know the same directories the tracked file
# grants. It cannot read that file — it runs with no resolved repo root, which is
# the ceiling on its own rule — so this test is what holds the two together.
@test "the guard's repo-script directories track the project grants" {
  local guarded dir granted covered
  guarded=$(sed -n 's/^REPO_SCRIPT_DIRS=(\(.*\))$/\1/p' "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ -n "$guarded" ]

  for granted in $(project_granted_dirs); do
    [[ " $guarded " == *" $granted "* ]] || {
      echo "granted but unknown to REPO_SCRIPT_DIRS: $granted"
      return 1
    }
  done

  # The reverse: a directory the guard steers toward with no grant behind it
  # trades one prompt for another. A narrower entry counts as covered by the
  # wildcard it sits under — `bin/local` by `Bash(bin/*)`.
  for dir in $guarded; do
    covered=
    for granted in $(project_granted_dirs); do
      if [[ "$dir" == "$granted" || "$dir" == "$granted"/* ]]; then covered=1; fi
    done
    [ -n "$covered" ] || { echo "REPO_SCRIPT_DIRS names an ungranted directory: $dir"; return 1; }
  done
}

@test "validate-permissions checks the tracked project settings file" {
  run "$REPO_ROOT/bin/local/validate-permissions"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
  [[ "$output" == *".claude/settings.json"* ]] || {
    echo "discovery did not reach .claude/settings.json:"
    echo "$output"
    return 1
  }
}

# The scaffold owns the list of files a project's .claude/ keeps out of git.
# This repo's .claude/ is hand-written rather than scaffolded, so nothing else
# holds the two in step.
@test "the tracked .claude/.gitignore matches the scaffold's artifact list" {
  local scaffolded
  scaffolded=$(sed -n 's/^CLAUDE_LOCAL_ARTIFACTS=(\(.*\))$/\1/p' "$REPO_ROOT/ai/claude/steps.sh")
  [ -n "$scaffolded" ]

  local artifact
  for artifact in $scaffolded; do
    grep -qxF "$artifact" "$REPO_ROOT/.claude/.gitignore" || {
      echo "scaffolded artifact missing from .claude/.gitignore: $artifact"
      return 1
    }
  done
}

# ── Registry permissions are injected at sync ────────────────────────────────
# Sync is the only path that derives them, so it is the only place coverage can
# be asserted — the committed template must stay free of them, or the two halves
# drift apart again.

@test "registry-derived permissions reach the synced settings file" {
  [ -f "$SYNCED" ] || { echo "sync produced no $SYNCED"; return 1; }
  local -a registry_perms=()
  mapfile -t registry_perms < "$BATS_FILE_TMPDIR/registry_perms.list"
  [ "${#registry_perms[@]}" -gt 0 ]
  local perm
  for perm in "${registry_perms[@]}"; do
    run jq -e --arg p "$perm" '.permissions.allow | index($p) != null' "$SYNCED"
    [ "$status" -eq 0 ] || { echo "missing from synced permissions.allow: $perm"; return 1; }
  done
}

@test "the committed template carries no registry-derived permission" {
  local -a registry_perms=()
  mapfile -t registry_perms < "$BATS_FILE_TMPDIR/registry_perms.list"
  [ "${#registry_perms[@]}" -gt 0 ]
  local perm
  for perm in "${registry_perms[@]}"; do
    run jq -e --arg p "$perm" '.permissions.allow | index($p) != null' "$SETTINGS"
    [ "$status" -ne 0 ] || { echo "registry permission committed to the template: $perm"; return 1; }
  done
}

# validate-permissions discovers only committed settings files, so the
# registry-derived half is checked here against the file they actually land in.
@test "every rule in the synced settings file can match a command" {
  run "$REPO_ROOT/bin/local/validate-permissions" --quiet "$SYNCED"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

# ── npm outward-facing subcommands ────────────────────────────────────────────
# Bash(npm:*) is allowed because install/run/ci are local and reversible, the
# same call the already-trusted pip3:* makes. The subcommands that publish to a
# registry or write credentials are not, so each is denied by name — deny takes
# precedence over allow. Dropping one silently re-permits it.

@test "npm outward-facing subcommands are denied despite the npm wildcard" {
  run jq -e '.permissions.allow | index("Bash(npm:*)")' "$SETTINGS"
  [ "$status" -eq 0 ]
  local sub
  for sub in publish unpublish deprecate owner access dist-tag token login adduser "config set"; do
    run jq -e --arg r "Bash(npm $sub:*)" '.permissions.deny | index($r)' "$SETTINGS"
    [ "$status" -eq 0 ] || { echo "npm $sub not denied"; return 1; }
  done
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
# form prompts on every call. The rule covers every bin/ on the default PATH,
# plus a version manager's shim and install dirs. It rides the same quote-
# stripped text as the guardrails below, so such a path inside a quoted
# argument is not mistaken for an invocation.

@test "reposcript hook: blocks an absolute path to a bin/local script" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "reposcript hook: blocks an absolute path after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "reposcript hook: names the git/ prefixed path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/git/bin/local/generate-git-rules"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'git/bin/local/generate-git-rules'"* ]]
}

# ai/claude/bin sits under a `bin/` of its own, so the longest granted directory
# has to win — naming it `bin/review-post` would suggest a path that does not
# exist.
@test "reposcript hook: names the ai/claude/bin path in full" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/ai/claude/bin/review-post --pr 1"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'ai/claude/bin/review-post'"* ]]
}

@test "reposcript hook: blocks a ./ prefix on a top-level bin script" {
  run _run_guard '{"tool_input":{"command":"./bin/otto-workbench sync"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'bin/otto-workbench'"* ]]
}

@test "reposcript hook: blocks a ./ prefix on an ai/claude/bin script" {
  run _run_guard '{"tool_input":{"command":"./ai/claude/bin/otto-log stats"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'ai/claude/bin/otto-log'"* ]]
}

@test "reposcript hook: allows the relative form" {
  run _run_guard '{"tool_input":{"command":"bin/local/validate-all"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: allows the relative form of a top-level bin script" {
  run _run_guard '{"tool_input":{"command":"bin/otto-workbench sync"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: allows an absolute path inside a quoted argument" {
  run _run_guard '{"tool_input":{"command":"git commit -m \"drop; /Users/me/repo/bin/local/old\""}}'
  [ "$status" -eq 0 ]
}

# A bare `bin/` is claimed only behind a `./`. Absolutely spelled it belongs to
# whatever tree the path names, and a virtualenv ships one — suggesting
# `bin/pip` there would send Claude at a script that does not exist.
@test "reposcript hook: leaves an absolute virtualenv bin path alone" {
  run _run_guard '{"tool_input":{"command":"/tmp/fonttools-venv/bin/pip install fonttools"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: defers a PATH bin dir to the bare-name rule" {
  run _run_guard '{"tool_input":{"command":"/opt/homebrew/bin/gh pr view 42"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'gh'"* ]]
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

@test "pathbin hook: blocks a mise install bin and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/share/mise/installs/node/24.18.1/bin/node --test a.mjs"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: blocks a mise shim path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/share/mise/shims/node --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: blocks an asdf shim path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.asdf/shims/python --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'python'"* ]]
}

@test "pathbin hook: allows a mise install path as an argument" {
  run _run_guard '{"tool_input":{"command":"rtk ls /Users/me/.local/share/mise/installs/node/24.18.1/bin/"}}'
  [ "$status" -eq 0 ]
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
# command — a leading no-op token must not be a way around them, and neither is
# a second line. Heredoc bodies are dropped before any rule runs, so content
# being written to a file is not scanned as if it were the command itself. The
# quote-stripping ceiling is documented in the guard script itself.

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

# ── multi-line scanning ─────────────────────────────────────────────────────
# A multi-line command is several commands, and the second prompts as loudly as
# the first. Lines are joined with `; ` before the anchored rules run, which is
# why a lone `cd <dir>` — the sanctioned form — still has no separator to match.

@test "scan: blocks a pathbin invocation on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\n/Users/me/.local/share/mise/installs/node/24.18.1/bin/node --test a.mjs"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "scan: blocks an env-var prefix on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\nNODEBIN=/tmp/x/node\ntrue"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"VAR=value"* ]]
}

@test "scan: does not end a plain heredoc on an indented marker word" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\n  EOF\ntrue; FOO=bar\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "scan: ends a dash heredoc on an indented marker" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<-EOF\nbody\n  EOF\ntrue; FOO=bar"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"VAR=value"* ]]
}

@test "scan: still allows a bare cd as its own call" {
  run _run_guard '{"tool_input":{"command":"cd /Users/me/git/repo"}}'
  [ "$status" -eq 0 ]
}

@test "scan: treats a following line as a compound cd" {
  run _run_guard '{"tool_input":{"command":"cd /tmp/x\nls -la"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Compound cd"* ]]
}

# ── sh -c wrappers ──────────────────────────────────────────────────────────
# The analyzer cannot see inside the quoted payload, so it prompts for the
# wrapper as a whole and the offered rule keys on that exact string. The guard
# cannot see inside either — double-quoted spans are stripped before it runs.

@test "dashc hook: blocks sh -c wrapping a compound cd" {
  run _run_guard '{"tool_input":{"command":"sh -c \"cd /tmp/x; node --test a.mjs\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"sh -c"* ]]
}

@test "dashc hook: blocks bash -c on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\nbash -c \"ls -la\""}}'
  [ "$status" -eq 2 ]
}

@test "dashc hook: blocks a combined flag form" {
  run _run_guard '{"tool_input":{"command":"sh -ec \"ls\""}}'
  [ "$status" -eq 2 ]
}

@test "dashc hook: blocks a path-prefixed shell and names the wrapper" {
  run _run_guard '{"tool_input":{"command":"/bin/sh -c \"ls -la\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"sh -c"* ]]
}

@test "dashc hook: allows running a shell on a script file" {
  run _run_guard '{"tool_input":{"command":"bash /tmp/x/probe.sh"}}'
  [ "$status" -eq 0 ]
}

@test "dashc hook: allows a command merely containing sh" {
  run _run_guard '{"tool_input":{"command":"shellcheck -c /tmp/x/probe.sh"}}'
  [ "$status" -eq 0 ]
}

# ── file-writing redirects ──────────────────────────────────────────────────
# A Bash redirect is gated per write path, while the Edit and Write tools are
# allow-listed outright — so this rule steers to a different tool, not a
# different command. Only echo and printf are matched: a redirect capturing
# another command's output has no tool equivalent and must keep working.

@test "write hook: blocks printf appending to a repo file" {
  run _run_guard '{"tool_input":{"command":"printf -- '"'"'-- regen\\n'"'"' >> /Users/me/git/svc/schema/.latest.sql"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Write tool"* ]]
  [[ "$output" == *".latest.sql"* ]]
}

@test "write hook: blocks echo truncating a relative path" {
  run _run_guard '{"tool_input":{"command":"echo hello > notes.md"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Edit tool"* ]]
}

@test "write hook: blocks a redirect after a leading statement" {
  run _run_guard '{"tool_input":{"command":"ls; printf x >> notes.md"}}'
  [ "$status" -eq 2 ]
}

@test "write hook: allows a redirect to /dev/null" {
  run _run_guard '{"tool_input":{"command":"echo probe > /dev/null"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows a redirect to a scratch path" {
  run _run_guard '{"tool_input":{"command":"printf ok >> /tmp/probe.log"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows capturing another command's output" {
  run _run_guard '{"tool_input":{"command":"jq -r .name /Users/me/pkg.json > /Users/me/out.txt"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: blocks a quoted destination path" {
  run _run_guard "{\"tool_input\":{\"command\":\"echo hi > '/Users/me/my notes.md'\"}}"
  [ "$status" -eq 2 ]
  [[ "$output" == *"a quoted path"* ]]
}

@test "write hook: allows a quoted argument before a scratch destination" {
  run _run_guard '{"tool_input":{"command":"echo \"hello world\" > /tmp/probe.log"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows stderr redirection with no file target" {
  run _run_guard '{"tool_input":{"command":"echo probe 2>&1 | head -1"}}'
  [ "$status" -eq 0 ]
}

# ── backgrounding ───────────────────────────────────────────────────────────
# A `&` that stands alone backgrounds a shell the tool cannot reach, so the
# cleanup is a pkill. The neighbours matter: `&&`, `2>&1`, `&>` and `|&` all
# contain an ampersand and none of them background anything.

@test "background hook: blocks a trailing &" {
  run _run_guard '{"tool_input":{"command":"npm --prefix site run dev &"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"run_in_background"* ]]
}

@test "background hook: blocks a background start followed by a probe" {
  run _run_guard '{"tool_input":{"command":"python3 -m http.server 8931 & sleep 2; curl -sI localhost:8931"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: blocks a background start on its own line" {
  run _run_guard '{"tool_input":{"command":"ignore/serve.py &\ncurl -sI localhost:8000"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: blocks nohup" {
  run _run_guard '{"tool_input":{"command":"nohup node serve-out.mjs"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: allows a && conjunction" {
  run _run_guard '{"tool_input":{"command":"git fetch origin main && git status"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a 2>&1 redirect" {
  run _run_guard '{"tool_input":{"command":"bats tests/claude_settings.bats 2>&1 | tail -5"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a &> redirect" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ &> /tmp/out.log"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a |& pipe" {
  run _run_guard '{"tool_input":{"command":"shellcheck bin/local/validate-all |& head -20"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a case statement fallthrough" {
  run _run_guard '{"tool_input":{"command":"case release in r*) echo tag ;& *) echo done ;; esac"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows an ampersand inside a quoted argument" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -n 'a & b' /tmp/probe.txt\"}}"
  [ "$status" -eq 0 ]
}

# ── shell variable expansion ────────────────────────────────────────────────
# A `$VAR` reference is flagged as "simple_expansion" and prompts every time.
# This rule reads text stripped of single-quoted spans only: `'$HOME'` is a
# literal, `"$f"` expands, so only the latter may be mistaken for one.

@test "expand hook: blocks a for loop over a file list" {
  run _run_guard '{"tool_input":{"command":"for f in a.sql b.sql; do echo \"### $f\"; rtk read \"$f\"; done"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"simple_expansion"* ]]
  [[ "$output" == *"tail -n +1"* ]]
}

@test "expand hook: blocks a bare \$VAR outside quotes" {
  run _run_guard '{"tool_input":{"command":"echo $HOME"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"printenv"* ]]
}

@test "expand hook: blocks the \${VAR} brace form" {
  run _run_guard '{"tool_input":{"command":"ls ${HOME}/git"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"simple_expansion"* ]]
}

@test "expand hook: allows a \$VAR inside single quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -n '\\\$HOME' /tmp/x/f\"}}"
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a perl one-liner whose regex ends in \$" {
  run _run_guard "{\"tool_input\":{\"command\":\"perl -0pi -e 's/foo\\\$/bar/' /tmp/x/f\"}}"
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a \$VAR inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\necho \"$HOME\"\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a command with no expansion" {
  run _run_guard '{"tool_input":{"command":"tail -n +1 a.sql b.sql c.sql"}}'
  [ "$status" -eq 0 ]
}

@test "expand hook: defers to the compound cd rule" {
  run _run_guard '{"tool_input":{"command":"cd /tmp/x && echo \"$HOME\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Compound cd"* ]]
}

@test "cd hook: blocks a compound cd after a leading token" {
  run _run_guard '{"tool_input":{"command":"mkdir -p /tmp/x; cd /tmp/x && ls"}}'
  [ "$status" -eq 2 ]
}

@test "cd hook: allows a cd nested inside a quoted argument" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -rn 'cd /tmp/x && ls' /tmp/x; true\"}}"
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

@test "reposcript hook: allows an absolute bin/local path inside a heredoc body" {
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
