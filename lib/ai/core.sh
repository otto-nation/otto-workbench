#!/usr/bin/env bash
# Shared configuration and helpers for AI-powered git automation.
# Sourced by lib/ai/commit.sh, lib/ai/pr.sh, and lib/ai/review.sh via Taskfile tasks.
#
# State set by functions: AI_COMMAND, AI_RESPONSE

# ─── Configuration ────────────────────────────────────────────────────────────
# shellcheck disable=SC2034  # All config variables are used by sourcing scripts (commit.sh, pr.sh, review.sh)

# Git convention constants (COMMIT_TYPES, COMMIT_HEADER_MAX_LEN, COMMIT_BODY_MAX_LEN)
# are defined in lib/conventions.sh — sourced here so AI automation inherits them.
# When sourced from bash (bin scripts), BASH_SOURCE resolves the path.
# When sourced from sh (Taskfile tasks), TASKFILE_DIR is set by go-task.
if [ -n "${BASH_SOURCE:-}" ]; then
  _ai_core_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  _ai_core_dir="${TASKFILE_DIR:?lib/ai/core.sh requires BASH_SOURCE or TASKFILE_DIR}/lib/ai"
fi
# shellcheck source=../conventions.sh
. "$_ai_core_dir/../conventions.sh"
unset _ai_core_dir

# Maximum characters of diff content sent to the AI.
# Large diffs cause the AI CLI to reject the prompt entirely.
# When exceeded, complete per-file diffs are included greedily (smallest first);
# omitted files are listed by name so the AI still knows the full scope of changes.
DIFF_MAX_CHARS=8000

# When true, skips both issue-related prompts in generate_pr_content.
# Set by parse_pr_flags; pass --no-issue after -- in task invocations.
SKIP_ISSUE=false

# Git remote name used for push/fetch/range operations.
GIT_REMOTE="origin"

# Global env file path — single source of truth is lib/constants.sh (TASKFILE_ENV).
# When sourced via Taskfile tasks (sh, not bash), lib/constants.sh is not available,
# so we fall back to the same value defined there.
: "${TASKFILE_ENV:="$HOME/.config/task/taskfile.env"}"

# Local per-project override takes priority over the global TASKFILE_ENV.
AI_LOCAL_ENV_PATH=".taskfile/taskfile.env"

# Markers the AI must use when returning PR content.
# Must stay in sync with the prompt in generate_pr_content.
PR_TITLE_MARKER="TITLE:"
PR_DESCRIPTION_MARKER="DESCRIPTION:"
# ──────────────────────────────────────────────────────────────────────────────

# _resolve_env_file — finds the active env file (local override or global).
# Prints the path to stdout. Returns 1 if neither exists.
_resolve_env_file() {
  if [ -f "$AI_LOCAL_ENV_PATH" ]; then
    echo "$AI_LOCAL_ENV_PATH"
  elif [ -f "$TASKFILE_ENV" ]; then
    echo "$TASKFILE_ENV"
  else
    return 1
  fi
}

# load_ai_command
# Finds the AI config and validates the binary exists.
# Sets AI_COMMAND. Returns 1 on failure.
load_ai_command() {
  local env_file
  env_file=$(_resolve_env_file) || {
    echo "✗ AI not configured. Run: task --global ai:setup"
    return 1
  }

  if ! grep -q "^AI_COMMAND=" "$env_file"; then
    printf "✗ AI_COMMAND not set in %s\n" "$env_file"
    return 1
  fi

  AI_COMMAND=$(grep "^AI_COMMAND=" "$env_file" | head -1 | cut -d'=' -f2-)
  local ai_bin
  ai_bin=$(echo "$AI_COMMAND" | cut -d' ' -f1)

  if ! command -v "$ai_bin" >/dev/null 2>&1; then
    printf "✗ AI command not found: %s\n" "$ai_bin"
    return 1
  fi

  # Optionally export ANTHROPIC_API_KEY for automation billing isolation.
  # When set in taskfile.env it overrides the interactive session key for this task run only.
  if grep -q "^ANTHROPIC_API_KEY=" "$env_file" 2>/dev/null; then
    # shellcheck disable=SC2034  # ANTHROPIC_API_KEY is read by the AI CLI subprocess
    ANTHROPIC_API_KEY=$(grep "^ANTHROPIC_API_KEY=" "$env_file" | head -1 | cut -d'=' -f2-)
    export ANTHROPIC_API_KEY
  fi
}

# _detect_gh_org — extracts the GitHub org/owner from the current repo's origin remote.
# Handles SSH (git@github.com:org/repo.git) and HTTPS (https://github.com/org/repo.git).
# Prints the org name to stdout. Returns empty (not failure) if detection is not possible.
_detect_gh_org() {
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 0
  local org=""
  case "$url" in
    git@github.com:*)
      # git@github.com:org/repo.git → strip prefix and /repo.git suffix
      org="${url#git@github.com:}"
      org="${org%%/*}"
      ;;
    https://github.com/*)
      # https://github.com/org/repo.git → strip prefix and /repo.git suffix
      org="${url#https://github.com/}"
      org="${org%%/*}"
      ;;
  esac
  printf '%s' "$org"
}

# _normalize_org_to_env ORG — converts a GitHub org name to an env var suffix.
# Uppercases the name and replaces hyphens with underscores.
# Example: otto-nation → OTTO_NATION
_normalize_org_to_env() {
  local org="$1"
  printf '%s' "$org" | tr '[:lower:]-' '[:upper:]_'
}

# load_gh_token
# Resolves GH_TOKEN with per-org routing support.
# Resolution order (first match wins):
#   1. GH_TOKEN in local .taskfile/taskfile.env (project-level pin)
#   2. GH_TOKEN__<ORG> in global taskfile.env (org-specific)
#   3. GH_TOKEN in global taskfile.env (default)
#   4. GH_TOKEN already in the environment (e.g. CI, .env.local)
#   5. Fail with actionable error
# Returns 1 on failure.
load_gh_token() {
  local env_file
  env_file=$(_resolve_env_file) || true

  # Tier 1: local override (GH_TOKEN in .taskfile/taskfile.env)
  if [ -f "$AI_LOCAL_ENV_PATH" ] && grep -q "^GH_TOKEN=" "$AI_LOCAL_ENV_PATH"; then
    GH_TOKEN=$(grep "^GH_TOKEN=" "$AI_LOCAL_ENV_PATH" | head -1 | cut -d'=' -f2-)
    export GH_TOKEN
    return 0
  fi

  # Detect current org for org-specific token lookup
  local org org_suffix org_token_var
  org=$(_detect_gh_org)
  if [ -n "$org" ]; then
    org_suffix=$(_normalize_org_to_env "$org")
    org_token_var="GH_TOKEN__${org_suffix}"

    # Tier 2: org-specific token in global env file
    if [ -n "${env_file:-}" ] && grep -q "^${org_token_var}=" "$env_file"; then
      GH_TOKEN=$(grep "^${org_token_var}=" "$env_file" | head -1 | cut -d'=' -f2-)
      export GH_TOKEN
      return 0
    fi
  fi

  # Tier 3: default GH_TOKEN in env file
  if [ -n "${env_file:-}" ] && grep -q "^GH_TOKEN=" "$env_file"; then
    GH_TOKEN=$(grep "^GH_TOKEN=" "$env_file" | head -1 | cut -d'=' -f2-)
    export GH_TOKEN
    return 0
  fi

  # Tier 4: accept a token already present in the environment (e.g. CI system
  # or set manually) — as long as it was explicitly set, segmentation is maintained.
  if [ -n "${GH_TOKEN:-}" ]; then
    return 0
  fi

  # Tier 5: fail with actionable error
  local cfg_path="${env_file:-$TASKFILE_ENV}"
  printf "✗ GH_TOKEN not configured for AI automation.\n"
  if [ -n "${org:-}" ]; then
    printf "  Set %s (for %s) or GH_TOKEN (default) in %s\n" "$org_token_var" "$org" "$cfg_path"
  else
    printf "  Set GH_TOKEN in %s\n" "$cfg_path"
  fi
  printf "  Create a fine-grained PAT: https://github.com/settings/tokens/new\n"
  printf "  Required: Contents (read/write), Pull requests (read/write) — scoped to specific repos\n"
  printf "  Run: task --global ai:setup\n"
  return 1
}

# run_ai PROMPT [AGENT_OVERRIDE]
# Requires AI_COMMAND.
# When AGENT_OVERRIDE is provided, replaces --agent <name> in AI_COMMAND
# so different tasks can route to the appropriate agent.
# Sets AI_RESPONSE.
run_ai() {
  local prompt="$1"
  local agent_override="${2:-}"

  local cmd="$AI_COMMAND"
  if [[ -n "$agent_override" ]]; then
    # Replace the agent name after --agent with the override value.
    # Uses sed because bash parameter expansion cannot match [^ ]* (non-space glob).
    # shellcheck disable=SC2001
    cmd=$(echo "$cmd" | sed "s/--agent [^ ]*/--agent $agent_override/")
  fi

  # shellcheck disable=SC2086  # $cmd holds "binary [flags]"; word-splitting is intentional
  # Redirect stderr to /dev/null — MCP server errors and CLI noise must not pollute
  # the captured response. load_ai_command already validated the binary exists.
  # Strip complete ANSI sequences (ESC + '[' + params + letter) before removing bare control chars.
  # Anchoring to \033 prevents the pattern from eating markdown checkboxes like [x] or [ ].
  # shellcheck disable=SC2034  # AI_RESPONSE is read by callers after run_ai returns
  AI_RESPONSE=$(echo "$prompt" | $cmd 2>/dev/null | \
    sed 's/\033\[[0-9;]*[a-zA-Z]//g' | \
    tr -d '\033\007\015' | \
    sed 's/^[> ]*//g' | \
    sed '/^```/d')
}
