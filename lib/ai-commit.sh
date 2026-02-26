#!/bin/bash
# Shared helpers for AI-powered git automation.
# Sourced by tasks via: . "{{.TASKFILE_DIR}}/lib/ai-commit.sh"
#
# Typical call sequence:
#   load_ai_command          → sets AI_COMMAND
#   find_commitlint_config   → sets COMMITLINT_CONFIG
#   build_commit_rules       → sets COMMIT_RULES (derived from COMMITLINT_CONFIG)
#   generate_commit_msg DIFF → sets AI_MSG
#   validate_commit_msg MSG  → validates; returns 1 on failure
#   generate_pr_content BRANCH DEFAULT → sets PR_TITLE, PR_DESCRIPTION
#
# State set by functions: AI_COMMAND, COMMITLINT_CONFIG, COMMIT_RULES,
#                         AI_RESPONSE, AI_MSG, PR_TITLE, PR_DESCRIPTION

# ─── Configuration ────────────────────────────────────────────────────────────
# Maximum length of the commit header (type + optional scope + colon + space + subject).
# Enforced in both the AI prompt and the fallback validator.
COMMIT_HEADER_MAX_LEN=72

# Maximum length of each line in the commit body.
# Referenced in the AI prompt only — not machine-validated locally.
COMMIT_BODY_MAX_LEN=100

# Space-separated list of allowed commit types.
# Used to build the AI prompt rules and the fallback format validator.
# To add a type, append it here — no other changes needed.
COMMIT_TYPES="feat fix perf deps revert docs style refactor test build ci chore"
# ──────────────────────────────────────────────────────────────────────────────

# load_ai_command
# Finds the AI config and validates the binary exists.
# Sets AI_COMMAND. Returns 1 on failure.
load_ai_command() {
  local local_env=".taskfile/taskfile.env"
  local global_env="$HOME/.config/task/taskfile.env"
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
}

# find_commitlint_config
# Sets COMMITLINT_CONFIG to the first config found, or empty string if none.
find_commitlint_config() {
  COMMITLINT_CONFIG=""
  local configs=(
    "commitlint.config.js"
    "commitlint.config.mjs"
    "commitlint.config.cjs"
    ".github/.commitlintrc.mjs"
    ".github/.commitlintrc.json"
    ".commitlintrc.mjs"
    ".commitlintrc.json"
    ".commitlintrc.js"
  )
  for cfg in "${configs[@]}"; do
    if [ -f "$cfg" ]; then
      COMMITLINT_CONFIG="$cfg"
      return
    fi
  done
}

# build_commit_rules
# Requires COMMITLINT_CONFIG (set by find_commitlint_config).
# Sets COMMIT_RULES. Uses COMMIT_TYPES for the allowed-types list.
build_commit_rules() {
  if [ -n "$COMMITLINT_CONFIG" ]; then
    COMMIT_RULES="Follow the rules in this commitlint configuration: $(cat "$COMMITLINT_CONFIG")"
  else
    # Build a comma-separated display string from the space-separated COMMIT_TYPES constant
    local types_display
    types_display=$(echo "$COMMIT_TYPES" | tr ' ' ',')
    COMMIT_RULES="Follow these conventional commit rules:
- Use conventional commit format: type(scope): description
- Types: $types_display
- No period at end of subject
- Use semicolon (;) to separate multiple changes in header
- Separate header and body with blank line
- Use bullet points for multiple changes in body"
  fi
}

# run_ai PROMPT
# Requires AI_COMMAND.
# Sets AI_RESPONSE.
run_ai() {
  local prompt="$1"
  # shellcheck disable=SC2086  # $AI_COMMAND holds "binary [flags]"; word-splitting is intentional
  # Strip complete ANSI sequences (ESC + '[' + params + letter) before removing bare control chars.
  # Anchoring to \033 prevents the pattern from eating markdown checkboxes like [x] or [ ].
  AI_RESPONSE=$(echo "$prompt" | $AI_COMMAND | \
    sed 's/\033\[[0-9;]*[a-zA-Z]//g' | \
    tr -d '\033\007\015' | \
    sed 's/^[> ]*//g' | \
    sed '/^```/d')
}

# _build_commit_prompt DIFF FILES_SECTION [RETRY_PREAMBLE]
# Internal helper. Builds and runs the AI prompt; sets AI_MSG.
_build_commit_prompt() {
  local diff_content="$1"
  local files_section="$2"
  local retry_preamble="${3:-}"

  local ai_prompt="${retry_preamble}Generate a conventional commit message based on the changes.

CRITICAL REQUIREMENTS:
- Header MUST be ≤${COMMIT_HEADER_MAX_LEN} characters total
- Header = type + optional \"\(scope\)\" + \": \" + subject
- Subject budget = ${COMMIT_HEADER_MAX_LEN} minus your prefix length
  Example: \"feat\(auth\): \" is 12 chars -> subject must be <=60 chars
  Example: \"fix: \" is 5 chars -> subject must be <=67 chars
  Example: \"refactor\(payments\): \" is 20 chars -> subject must be <=52 chars
- Before writing, count your prefix length, subtract from ${COMMIT_HEADER_MAX_LEN}, then write a subject within that budget
- Each body line MUST be ≤${COMMIT_BODY_MAX_LEN} characters (wrap long lines)
- Subject must be concise — focus on WHAT changed, not HOW
- If multiple changes, use semicolon in subject or list in body

$COMMIT_RULES

${files_section}Diff:
$diff_content

Return only the raw commit message text. No markdown, no code blocks, no backticks, no explanation."

  run_ai "$ai_prompt"
  AI_MSG="$AI_RESPONSE"
}

# generate_commit_msg DIFF [FILE_LIST]
# Requires AI_COMMAND and COMMIT_RULES.
# Sets AI_MSG. Retries once with a precise character budget if the header exceeds
# COMMIT_HEADER_MAX_LEN. Returns 1 if the retry also fails — LLMs cannot reliably
# count characters, so the caller should surface the failure rather than proceeding
# with an invalid message.
generate_commit_msg() {
  local diff_content="$1"
  local file_list="${2:-}"
  local files_section=""

  if [ -n "$file_list" ]; then
    files_section="Files changed: $file_list

"
  fi

  _build_commit_prompt "$diff_content" "$files_section"

  local header header_len
  header=$(echo "$AI_MSG" | head -1)
  header_len=${#header}

  if [ "$header_len" -gt "$COMMIT_HEADER_MAX_LEN" ]; then
    # Extract the prefix the AI chose (e.g. "feat(auth): ") to give an exact subject budget
    local prefix subject_budget
    prefix=$(echo "$header" | grep -oE '^[^:]+: ')
    subject_budget=$(( COMMIT_HEADER_MAX_LEN - ${#prefix} ))

    echo "→ Header too long ($header_len chars), retrying with exact budget..."
    local retry_preamble="PREVIOUS ATTEMPT FAILED: '${header}' is ${header_len} characters — $(( header_len - COMMIT_HEADER_MAX_LEN )) over the limit.

You used the prefix '${prefix}' (${#prefix} chars). That leaves EXACTLY ${subject_budget} characters for the subject. Write a subject of ${subject_budget} characters or fewer. Count every character. Use the same prefix unless it genuinely does not fit.

"
    _build_commit_prompt "$diff_content" "$files_section" "$retry_preamble"

    header=$(echo "$AI_MSG" | head -1)
    header_len=${#header}
    if [ "$header_len" -gt "$COMMIT_HEADER_MAX_LEN" ]; then
      err "Could not generate a valid commit message after 2 attempts."
      echo "  Last attempt ($header_len chars): $header"
      echo "  Edit and commit manually: git commit -m \"<message>\""
      return 1
    fi
  fi
}

# validate_commit_msg MSG
# Requires COMMITLINT_CONFIG (set by find_commitlint_config).
# Uses commitlint when available; falls back to a basic header length check.
# Returns 1 on validation failure.
validate_commit_msg() {
  local msg="$1"
  if [ -n "$COMMITLINT_CONFIG" ] && command -v npx &>/dev/null; then
    echo "→ Validating commit message..."
    if ! echo "$msg" | npx commitlint --config "$COMMITLINT_CONFIG" 2>&1; then
      echo "✗ Commit message failed commitlint validation"
      return 1
    fi
    echo "✓ Commit message validated"
    echo ""
  else
    local header
    header=$(echo "$msg" | head -1)
    local header_len=${#header}
    if [ "$header_len" -gt "$COMMIT_HEADER_MAX_LEN" ]; then
      echo "✗ Header is $header_len characters (max ${COMMIT_HEADER_MAX_LEN}): $header"
      return 1
    fi
    # Build regex from COMMIT_TYPES so it stays in sync with build_commit_rules
    local types_regex
    types_regex=$(echo "$COMMIT_TYPES" | tr ' ' '|')
    local commit_pattern="^(${types_regex})(\(.+\))?: .+"
    if ! echo "$header" | grep -qE "$commit_pattern"; then
      echo "✗ Header does not follow conventional commit format: $header"
      echo "  Expected: type(scope): description"
      return 1
    fi
  fi
}

# push_branch BRANCH
# Pushes BRANCH to remote, handling first-push and divergence cases.
# Returns 1 on any failure that should abort the caller.
push_branch() {
  local branch="$1"

  if ! git ls-remote --heads origin "$branch" | grep -q "$branch"; then
    echo "→ Pushing new branch to remote..."
    git push -u origin "$branch" || { echo "✗ Push failed"; return 1; }
    return 0
  fi

  git fetch origin "$branch" --quiet

  # If no tracking branch configured, just push
  if ! git rev-parse --verify "@{u}" &>/dev/null 2>&1; then
    git push origin "$branch" || { echo "✗ Push failed"; return 1; }
    return 0
  fi

  local local_sha remote_sha base_sha
  local_sha=$(git rev-parse @)
  # shellcheck disable=SC1083  # @{u} is git's upstream shorthand, not a shell construct
  remote_sha=$(git rev-parse @{u})
  # shellcheck disable=SC1083
  base_sha=$(git merge-base @ @{u})

  if [ "$local_sha" = "$remote_sha" ]; then
    echo "✓ Branch is up to date with remote"
  elif [ "$local_sha" = "$base_sha" ]; then
    echo "✗ Remote has commits not in local branch — please pull first: git pull"
    return 1
  elif [ "$remote_sha" = "$base_sha" ]; then
    echo "→ Local has unpushed commits, pushing..."
    git push origin "$branch" || { echo "✗ Push failed"; return 1; }
  else
    echo "✗ Branch has diverged from remote"
    echo "→ Fix with: git pull --rebase or git reset"
    return 1
  fi
}

# load_pr_context
# Loads the AI command and resolves the current branch context.
# Must be called before generate_pr_content or push_branch.
# Sets BRANCH and DEFAULT_BRANCH. Returns 1 on failure.
load_pr_context() {
  load_ai_command || return 1

  BRANCH=$(git branch --show-current)
  DEFAULT_BRANCH=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's@^origin/@@')
  DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"

  if [ "$BRANCH" = "$DEFAULT_BRANCH" ]; then
    echo "✗ PR operations cannot be run from the $DEFAULT_BRANCH branch"
    return 1
  fi
}

# generate_pr_content BRANCH DEFAULT_BRANCH
# Requires AI_COMMAND.
# Sets PR_TITLE and PR_DESCRIPTION.
generate_pr_content() {
  local branch="$1"
  local default_branch="$2"

  # Extract issue number from branch name (e.g., feature/ISSUE-123-description)
  local issue_number
  issue_number=$(echo "$branch" | grep -oE '[A-Z]+-[0-9]+' | head -1)

  if [ -z "$issue_number" ]; then
    echo "→ No issue number found in branch name: $branch"
    echo ""
    printf "  Enter issue number (e.g., ISSUE-123) or press Enter to skip: "
    read -r issue_number
  else
    echo "✓ Found issue number: $issue_number"
  fi

  local commits commit_count changed_files
  commits=$(git log --oneline "origin/$default_branch..HEAD")
  commit_count=$(git rev-list --count "origin/$default_branch..$branch")
  changed_files=$(git diff --name-only "origin/$default_branch..$branch")

  local has_template=false
  local pr_template pr_template_file=""
  # Check all locations GitHub recognises, in priority order
  for _candidate in \
    ".github/pull_request_template.md" \
    ".github/PULL_REQUEST_TEMPLATE.md" \
    "pull_request_template.md" \
    "PULL_REQUEST_TEMPLATE.md"; do
    if [ -f "$_candidate" ]; then
      pr_template_file="$_candidate"
      break
    fi
  done
  if [ -n "$pr_template_file" ]; then
    pr_template=$(cat "$pr_template_file")
    has_template=true
  else
    pr_template="## Summary

## Changes

## Testing"
  fi

  local ai_prompt="Generate a professional PR title and fill out this template based on the changes:

Template:
$pr_template

Branch: $branch
Issue: ${issue_number:-None}
Commits: $commit_count

Recent commits:
$commits

Changed files:
$changed_files

Return: TITLE: <title>
DESCRIPTION: <filled template>"

  run_ai "$ai_prompt"

  PR_TITLE=$(echo "$AI_RESPONSE" | grep "^TITLE:" | sed 's/^TITLE: //' | head -1 | tr -d '\n\r' | sed 's/^`//;s/`$//')
  PR_DESCRIPTION=$(echo "$AI_RESPONSE" | sed -n '/^DESCRIPTION:/,$ p' | sed '1d' | sed 's/^```markdown$//' | sed 's/^```$//')

  if [ -z "$PR_TITLE" ]; then PR_TITLE="feat: improve codebase"; fi
  if [ -z "$PR_DESCRIPTION" ]; then
    PR_DESCRIPTION="## Summary"$'\n\n'"Branch: $branch"$'\n'"Commits: $commit_count"
  fi

  # Only add "Closes" if no PR template and issue is a GitHub numeric issue
  if [ "$has_template" = "false" ] && [ -n "$issue_number" ]; then
    local clean_issue
    clean_issue=$(echo "$issue_number" | sed 's/^#//')
    if echo "$clean_issue" | grep -qE '^[0-9]+$'; then
      echo ""
      printf "  Close issue #%s when PR merges? [y/N] " "$clean_issue"
      read -r close_issue
      if [[ "$close_issue" =~ ^[Yy]$ ]]; then
        PR_DESCRIPTION="Closes #$clean_issue"$'\n\n'"$PR_DESCRIPTION"
      fi
    fi
  fi
}
