#!/bin/bash
# Shared configuration and helpers for AI-powered git automation.
# Sourced by lib/ai/commit.sh, lib/ai/pr.sh, and lib/ai/review.sh via Taskfile tasks.
#
# State set by functions: AI_COMMAND, AI_RESPONSE

# ─── Configuration ────────────────────────────────────────────────────────────
# shellcheck disable=SC2034  # All config variables are used by sourcing scripts (commit.sh, pr.sh, review.sh)
# Maximum length of the commit header (type + optional scope + colon + space + subject).
# Enforced in both the AI prompt and the fallback validator.
COMMIT_HEADER_MAX_LEN=72

# Maximum length of each line in the commit body.
# Referenced in the AI prompt only — not machine-validated locally.
COMMIT_BODY_MAX_LEN=100

# Maximum characters of diff content sent to the AI.
# Large diffs cause the AI CLI to reject the prompt entirely.
# When exceeded, complete per-file diffs are included greedily (smallest first);
# omitted files are listed by name so the AI still knows the full scope of changes.
DIFF_MAX_CHARS=8000

# Space-separated list of allowed commit types.
# Used to build the AI prompt rules and the fallback format validator.
# To add a type, append it here — no other changes needed.
COMMIT_TYPES="feat fix perf deps revert docs style refactor test build ci chore"

# When true, skips both issue-related prompts in generate_pr_content.
# Set by parse_pr_flags; pass --no-issue after -- in task invocations.
SKIP_ISSUE=false

# Git remote name used for push/fetch/range operations.
GIT_REMOTE="origin"

# Paths searched for the AI command configuration, local taking priority over global.
# AI_GLOBAL_ENV_SUBPATH is relative to $HOME and expanded at call time, not source time.
AI_LOCAL_ENV_PATH=".taskfile/taskfile.env"
AI_GLOBAL_ENV_SUBPATH=".config/task/taskfile.env"

# Markers the AI must use when returning PR content.
# Must stay in sync with the prompt in generate_pr_content.
PR_TITLE_MARKER="TITLE:"
PR_DESCRIPTION_MARKER="DESCRIPTION:"
# ──────────────────────────────────────────────────────────────────────────────

# load_ai_command
# Finds the AI config and validates the binary exists.
# Sets AI_COMMAND. Returns 1 on failure.
load_ai_command() {
  local local_env="$AI_LOCAL_ENV_PATH"
  local global_env="$HOME/$AI_GLOBAL_ENV_SUBPATH"
  local env_file

  if [ -f "$local_env" ]; then
    env_file="$local_env"
  elif [ -f "$global_env" ]; then
    env_file="$global_env"
  else
    echo "✗ AI not configured. Run: task --global ai:setup"
    return 1
  fi

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

# load_gh_token
# Reads GH_TOKEN from the taskfile.env config file and exports it.
# Falls back to an existing GH_TOKEN in the environment (e.g. set via .env.local or shell).
# Hard-fails if no token is available from either source — automation must not silently
# fall back to the user's full interactive gh session.
# Returns 1 on failure.
load_gh_token() {
  local local_env="$AI_LOCAL_ENV_PATH"
  local global_env="$HOME/$AI_GLOBAL_ENV_SUBPATH"
  local env_file

  if [ -f "$local_env" ]; then
    env_file="$local_env"
  elif [ -f "$global_env" ]; then
    env_file="$global_env"
  fi

  if [ -n "${env_file:-}" ] && grep -q "^GH_TOKEN=" "$env_file"; then
    GH_TOKEN=$(grep "^GH_TOKEN=" "$env_file" | head -1 | cut -d'=' -f2-)
    export GH_TOKEN
    return 0
  fi

  # Accept a token already present in the environment (e.g. exported from a CI system
  # or set manually) — as long as it was explicitly set, segmentation is maintained.
  if [ -n "${GH_TOKEN:-}" ]; then
    return 0
  fi

  local cfg_path="${env_file:-$HOME/$AI_GLOBAL_ENV_SUBPATH}"
  printf "✗ GH_TOKEN not configured for AI automation.\n"
  printf "  Set it in %s\n" "$cfg_path"
  printf "  Create a fine-grained PAT: https://github.com/settings/tokens/new\n"
  printf "  Required: Contents (read/write), Pull requests (read/write) — scoped to specific repos\n"
  printf "  Run: task --global ai:setup\n"
  return 1
}

# run_ai PROMPT
# Requires AI_COMMAND.
# Sets AI_RESPONSE.
run_ai() {
  local prompt="$1"
  # shellcheck disable=SC2086  # $AI_COMMAND holds "binary [flags]"; word-splitting is intentional
  # Redirect stderr to /dev/null — MCP server errors and CLI noise must not pollute
  # the captured response. load_ai_command already validated the binary exists.
  # Strip complete ANSI sequences (ESC + '[' + params + letter) before removing bare control chars.
  # Anchoring to \033 prevents the pattern from eating markdown checkboxes like [x] or [ ].
  # shellcheck disable=SC2034  # AI_RESPONSE is read by callers after run_ai returns
  AI_RESPONSE=$(echo "$prompt" | $AI_COMMAND 2>/dev/null | \
    sed 's/\033\[[0-9;]*[a-zA-Z]//g' | \
    tr -d '\033\007\015' | \
    sed 's/^[> ]*//g' | \
    sed '/^```/d')
}

# _compact_diff FULL_DIFF
# Splits a diff into per-file chunks and greedily includes complete file diffs
# within DIFF_MAX_CHARS (smallest files first, maximising coverage).
# Files that don't fit are listed by name in a trailing note.
_compact_diff() {
  local full_diff="$1"

  # Split diff into per-file chunks on "diff --git" boundaries
  local chunks=()
  local current=""
  while IFS= read -r line; do
    if [[ "$line" == "diff --git "* && -n "$current" ]]; then
      chunks+=("$current")
      current=""
    fi
    current+="${line}"$'\n'
  done <<< "$full_diff"
  [[ -n "$current" ]] && chunks+=("$current")

  local total=${#chunks[@]}
  if [[ $total -eq 0 ]]; then
    printf '%s' "${full_diff:0:$DIFF_MAX_CHARS}"
    return
  fi

  # Build "SIZE INDEX" pairs and sort ascending so smallest files are tried first
  local i size_index_pairs=""
  for (( i=0; i<total; i++ )); do
    size_index_pairs+="${#chunks[$i]} $i"$'\n'
  done

  local budget=$DIFF_MAX_CHARS
  local included_indices=()
  local omitted_names=()
  local size idx fname

  # Use temp files instead of process substitution — sh (used by task) disables < <(...).
  local _sort_tmp _idx_tmp
  _sort_tmp=$(mktemp)
  _idx_tmp=$(mktemp)
  printf '%s' "$size_index_pairs" | sort -n > "$_sort_tmp"
  while IFS=' ' read -r size idx; do
    [[ -z "$size" ]] && continue
    if (( size <= budget )); then
      included_indices+=("$idx")
      (( budget -= size ))
    else
      fname=$(printf '%s' "${chunks[$idx]}" | head -1 | grep -oE ' b/.+$' | sed 's/^ b\///')
      omitted_names+=("${fname:-<file>}")
    fi
  done < "$_sort_tmp"
  rm -f "$_sort_tmp"

  # Reconstruct in original diff order
  local result=""
  printf '%s\n' "${included_indices[@]}" | sort -n > "$_idx_tmp"
  while IFS= read -r idx; do
    [[ -z "$idx" ]] && continue
    result+="${chunks[$idx]}"
  done < "$_idx_tmp"
  rm -f "$_idx_tmp"

  if [[ ${#omitted_names[@]} -gt 0 ]]; then
    local omitted_list
    omitted_list=$(printf '%s\n' "${omitted_names[@]}" | paste -sd ',' -)
    result+="
[${#omitted_names[@]} file(s) omitted — diff too large: $omitted_list]"
  fi

  printf '%s' "$result"
}
