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
#   load_pr [ARGS]           → parses PR flags, sets AI_COMMAND, BRANCH, DEFAULT_BRANCH
#   generate_pr_content BRANCH DEFAULT → sets PR_TITLE, PR_DESCRIPTION
#
# State set by functions: AI_COMMAND, COMMITLINT_CONFIG, COMMIT_RULES,
#                         AI_RESPONSE, AI_MSG, PR_TITLE, PR_DESCRIPTION,
#                         BRANCH, DEFAULT_BRANCH, SKIP_ISSUE

# ─── Configuration ────────────────────────────────────────────────────────────
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
}

# find_commitlint_config
# Sets COMMITLINT_CONFIG to the first config found, or empty string if none.
# configuration files picked from: https://github.com/conventional-changelog/commitlint?tab=readme-ov-file#config
find_commitlint_config() {
  COMMITLINT_CONFIG=""
  local configs
  configs=(
    commitlint.config.{js,cjs,mjs,ts,cts,mts}
    .github/.commitlintrc
    .github/.commitlintrc.{json,yaml,yml,js,cjs,mjs,ts,cts,mts}
    .commitlintrc
    .commitlintrc.{json,yaml,yml,js,cjs,mjs,ts,cts,mts}
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
  # Redirect stderr to /dev/null — MCP server errors and CLI noise must not pollute
  # the captured response. load_ai_command already validated the binary exists.
  # Strip complete ANSI sequences (ESC + '[' + params + letter) before removing bare control chars.
  # Anchoring to \033 prevents the pattern from eating markdown checkboxes like [x] or [ ].
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

  while IFS=' ' read -r size idx; do
    [[ -z "$size" ]] && continue
    if (( size <= budget )); then
      included_indices+=("$idx")
      (( budget -= size ))
    else
      fname=$(printf '%s' "${chunks[$idx]}" | head -1 | grep -oE ' b/.+$' | sed 's/^ b\///')
      omitted_names+=("${fname:-<file>}")
    fi
  done < <(printf '%s' "$size_index_pairs" | sort -n)

  # Reconstruct in original diff order
  local result=""
  while IFS= read -r idx; do
    [[ -z "$idx" ]] && continue
    result+="${chunks[$idx]}"
  done < <(printf '%s\n' "${included_indices[@]}" | sort -n)

  if [[ ${#omitted_names[@]} -gt 0 ]]; then
    local omitted_list
    omitted_list=$(printf '%s\n' "${omitted_names[@]}" | paste -sd ',' -)
    result+="
[${#omitted_names[@]} file(s) omitted — diff too large: $omitted_list]"
  fi

  printf '%s' "$result"
}

# _build_commit_prompt DIFF FILES_SECTION [RETRY_PREAMBLE]
# Internal helper. Builds and runs the AI prompt; sets AI_MSG.
_build_commit_prompt() {
  local diff_content="$1"
  local files_section="$2"
  local retry_preamble="${3:-}"

  # When the diff exceeds the budget, include as many complete per-file diffs
  # as fit (smallest files first) so the AI always sees whole-file context.
  if [ "${#diff_content}" -gt "$DIFF_MAX_CHARS" ]; then
    diff_content=$(_compact_diff "$diff_content")
  fi

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
      local over=$(( header_len - COMMIT_HEADER_MAX_LEN ))
      # LLMs cannot reliably count characters. Accept messages that are marginally
      # over the limit (≤3 chars) when no commitlint config enforces it strictly.
      if [ "$over" -le 3 ] && [ -z "$COMMITLINT_CONFIG" ]; then
        echo "→ Header is ${header_len} chars (${over} over limit) — accepting without commitlint"
      else
        err "Could not generate a valid commit message after 2 attempts."
        echo "  Last attempt ($header_len chars): $header"
        echo "  Edit and commit manually: git commit -m \"<message>\""
        return 1
      fi
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

  if ! git ls-remote --heads "$GIT_REMOTE" "$branch" | grep -q "$branch"; then
    echo "→ Pushing new branch to remote..."
    git push -u "$GIT_REMOTE" "$branch" || { echo "✗ Push failed"; return 1; }
    return 0
  fi

  git fetch "$GIT_REMOTE" "$branch" --quiet

  # If no tracking branch configured, just push
  if ! git rev-parse --verify "@{u}" &>/dev/null 2>&1; then
    git push "$GIT_REMOTE" "$branch" || { echo "✗ Push failed"; return 1; }
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
    git push "$GIT_REMOTE" "$branch" || { echo "✗ Push failed"; return 1; }
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

  if ! gh auth status &>/dev/null; then
    echo "✗ GitHub CLI is not authenticated — run: gh auth login"
    return 1
  fi

  BRANCH=$(git branch --show-current)
  DEFAULT_BRANCH=$(git rev-parse --abbrev-ref "$GIT_REMOTE/HEAD" 2>/dev/null | sed "s@^$GIT_REMOTE/@@")
  DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"

  if [ "$BRANCH" = "$DEFAULT_BRANCH" ]; then
    echo "✗ PR operations cannot be run from the $DEFAULT_BRANCH branch"
    return 1
  fi
}

# parse_pr_flags ARGS
# Parses PR-specific flags from the CLI_ARGS string.
# Sets SKIP_ISSUE. Returns 1 on unknown flag.
parse_pr_flags() {
  local args="$1"
  SKIP_ISSUE=false
  local arg
  for arg in $args; do
    case "$arg" in
      --no-issue) SKIP_ISSUE=true ;;
      *) printf "✗ Unknown flag: %s\n" "$arg"; return 1 ;;
    esac
  done
}

# load_pr [ARGS]
# Parses PR flags from ARGS, then loads the PR context.
# Sets SKIP_ISSUE, AI_COMMAND, BRANCH, DEFAULT_BRANCH. Returns 1 on failure.
load_pr() {
  local args="${1:-}"
  parse_pr_flags "$args" || return 1
  load_pr_context || return 1
}

# _pr_resolve_issue BRANCH
# Extracts an issue number from the branch name (e.g. feat/PROJ-42-desc → PROJ-42).
# When none is found and SKIP_ISSUE is false, prompts the user to enter one.
# Sets PR_ISSUE.
_pr_resolve_issue() {
  local branch="$1"
  PR_ISSUE=$(echo "$branch" | grep -oE '[A-Z]+-[0-9]+' | head -1)

  if [ -z "$PR_ISSUE" ]; then
    if [[ "$SKIP_ISSUE" = "false" ]]; then
      echo "→ No issue number found in branch name: $branch"
      echo ""
      printf "  Enter issue number (e.g., ISSUE-123) or press Enter to skip: "
      read -r PR_ISSUE
    fi
  else
    echo "✓ Found issue number: $PR_ISSUE"
  fi
}

# _pr_load_template
# Finds a PR template in the GitHub-recognised locations (priority order).
# Falls back to a minimal Summary/Changes/Testing template when none is found.
# Sets PR_TEMPLATE and PR_HAS_TEMPLATE.
_pr_load_template() {
  PR_HAS_TEMPLATE=false
  PR_TEMPLATE=""
  local candidate
  for candidate in \
    ".github/pull_request_template.md" \
    ".github/PULL_REQUEST_TEMPLATE.md" \
    "pull_request_template.md" \
    "PULL_REQUEST_TEMPLATE.md"; do
    if [ -f "$candidate" ]; then
      PR_TEMPLATE=$(cat "$candidate")
      PR_HAS_TEMPLATE=true
      return
    fi
  done
  PR_TEMPLATE="## Summary

## Changes

## Testing"
}

# _pr_generate_single_commit CHANGED_FILES
# Handles the single-commit PR path: title is taken directly from the commit subject.
# When a PR template exists, AI fills it; otherwise the commit body is used as-is.
# Reads globals: PR_TEMPLATE, PR_HAS_TEMPLATE.
# Sets PR_TITLE and PR_DESCRIPTION.
_pr_generate_single_commit() {
  local changed_files="$1"
  local commit_subject commit_body
  commit_subject=$(git log -1 --format="%s")
  commit_body=$(git log -1 --format="%b" | sed '/^[[:space:]]*$/d')

  PR_TITLE="$commit_subject"

  if [[ "$PR_HAS_TEMPLATE" = "true" ]]; then
    echo "→ Single commit — using commit message as title, AI filling template"
    local ai_prompt="Fill out this PR template based on the commit below. Return only the filled template body — no title, no markers, no extra commentary.

Template:
$PR_TEMPLATE

Commit subject: $commit_subject
Commit body: ${commit_body:-<none>}

Changed files:
$changed_files"
    run_ai "$ai_prompt"
    PR_DESCRIPTION="${AI_RESPONSE:-$PR_TEMPLATE}"
  elif [[ -n "$commit_body" ]]; then
    echo "→ Single commit — skipping AI, using commit message directly"
    PR_DESCRIPTION="$commit_body"
  else
    echo "→ Single commit — skipping AI, using commit message directly"
    # shellcheck disable=SC2001  # multi-line prefix; parameter expansion not practical here
    PR_DESCRIPTION="## Summary

## Changes

$(echo "$changed_files" | sed 's/^/- /')

## Testing"
  fi
}

# _pr_generate_multi_commit BRANCH ISSUE COMMITS COMMIT_COUNT CHANGED_FILES
# Handles the multi-commit PR path: AI generates title and fills the PR template.
# Falls back to safe defaults when the AI response is missing or malformed.
# Reads globals: PR_TEMPLATE, PR_TITLE_MARKER, PR_DESCRIPTION_MARKER.
# Sets PR_TITLE and PR_DESCRIPTION.
_pr_generate_multi_commit() {
  local branch="$1" issue="$2" commits="$3" commit_count="$4" changed_files="$5"

  local ai_prompt="Generate a professional PR title and fill out this template based on the changes:

Template:
$PR_TEMPLATE

Branch: $branch
Issue: ${issue:-None}
Commits: $commit_count

Recent commits:
$commits

Changed files:
$changed_files

Return: $PR_TITLE_MARKER <title>
$PR_DESCRIPTION_MARKER <filled template>"

  run_ai "$ai_prompt"

  # shellcheck disable=SC2016  # backticks in single-quoted sed pattern are literal, not shell expansions
  PR_TITLE=$(echo "$AI_RESPONSE" | grep "^$PR_TITLE_MARKER" | sed "s/^$PR_TITLE_MARKER //" | head -1 | tr -d '\n\r' | sed 's/^`//;s/`$//')
  PR_DESCRIPTION=$(echo "$AI_RESPONSE" | sed -n "/^$PR_DESCRIPTION_MARKER/,$ p" | sed '1d' | sed 's/^```markdown$//' | sed 's/^```$//')

  [ -z "$PR_TITLE" ] && PR_TITLE="feat: improve codebase"
  [ -z "$PR_DESCRIPTION" ] && PR_DESCRIPTION="## Summary"$'\n\n'"Branch: $branch"$'\n'"Commits: $commit_count"
}

# _pr_append_issue_link ISSUE HAS_TEMPLATE
# Prepends "Closes #N" to PR_DESCRIPTION when the issue is a numeric GitHub issue,
# no PR template is active (templates handle linking themselves), and the user confirms.
# Modifies PR_DESCRIPTION in place.
_pr_append_issue_link() {
  local issue="$1" has_template="$2"
  [ "$has_template" = "true" ] || [ -z "$issue" ] || [ "$SKIP_ISSUE" = "true" ] && return

  local clean_issue
  clean_issue="${issue##\#}"
  echo "$clean_issue" | grep -qE '^[0-9]+$' || return

  echo ""
  printf "  Close issue #%s when PR merges? [y/N] " "$clean_issue"
  local close_issue
  read -r close_issue
  if [[ "$close_issue" =~ ^[Yy]$ ]]; then
    PR_DESCRIPTION="Closes #$clean_issue"$'\n\n'"$PR_DESCRIPTION"
  fi
}

# generate_pr_content BRANCH DEFAULT_BRANCH
# Requires AI_COMMAND.
# Sets PR_TITLE and PR_DESCRIPTION.
generate_pr_content() {
  local branch="$1"
  local default_branch="$2"

  _pr_resolve_issue "$branch"
  _pr_load_template

  local commits commit_count changed_files
  commits=$(git log --oneline "$GIT_REMOTE/$default_branch..HEAD")
  commit_count=$(git rev-list --count "$GIT_REMOTE/$default_branch..$branch")
  changed_files=$(git diff --name-only "$GIT_REMOTE/$default_branch..$branch")

  if [[ "$commit_count" -eq 1 ]]; then
    _pr_generate_single_commit "$changed_files"
  else
    _pr_generate_multi_commit "$branch" "$PR_ISSUE" "$commits" "$commit_count" "$changed_files"
  fi

  _pr_append_issue_link "$PR_ISSUE" "$PR_HAS_TEMPLATE"
}
