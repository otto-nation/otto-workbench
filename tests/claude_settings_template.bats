#!/usr/bin/env bats
# Tests for the Claude settings template, the tracked project allowlist, and registry-derived permissions.
# The template contains handwritten permissions only (shell builtins, filesystem
# ops). Tool permissions (gh, go, etc.) are derived from registry permission
# fields by step_claude_settings and only ever land in ~/.claude/settings.json,
# so the synced sandbox below — not the template — is where they are asserted.
#
# Grants for this repo's own scripts are neither: they live in the tracked
# .claude/settings.json, which applies to this repo alone and travels with every
# worktree. Those are asserted against that file.
setup_file() {
  load 'test_helper'
  load 'claude_settings_helper'
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
  load 'claude_settings_helper'
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
  for dir in bin git/bin ai/bin ai/claude/bin; do
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

# The pass line, not the exit status. Auto-discovery also reaches the untracked
# settings files — including the bare-repo container's, above every worktree —
# whose contents are this machine's state and can change between one test run
# and the next. A tick beside the tracked file says discovery reached it and it
# is clean, which is what this test is named for; the machine's own drift is
# `--fix`'s business, not this suite's.
@test "validate-permissions checks the tracked project settings file" {
  run "$REPO_ROOT/bin/local/validate-permissions"
  [[ "$output" == *"✓ .claude/settings.json"* ]] || {
    echo "no clean discovery of .claude/settings.json:"
    echo "$output"
    return 1
  }
}

# The scaffold owns the list of files a project's .claude/ keeps out of git.
# This repo's .claude/ is hand-written rather than scaffolded, so nothing else
# holds the two in step.
@test "the tracked .claude/.gitignore matches the scaffold's artifact list" {
  local scaffolded
  scaffolded=$(sed -n 's/^CLAUDE_LOCAL_ARTIFACTS=(\(.*\))$/\1/p' "$REPO_ROOT/ai/claude/scaffold.sh")
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
