#!/bin/bash
# PR generation helpers.
# Requires lib/ai/core.sh to be sourced first.
#
# Typical call sequence:
#   load_pr [ARGS]                          → sets SKIP_ISSUE, AI_COMMAND, BRANCH, DEFAULT_BRANCH
#   push_branch BRANCH                      → pushes branch if needed
#   generate_pr_content BRANCH DEFAULT      → sets PR_TITLE, PR_DESCRIPTION
#
# State set by functions: BRANCH, DEFAULT_BRANCH, SKIP_ISSUE, PR_ISSUE,
#                         PR_TEMPLATE, PR_HAS_TEMPLATE, PR_TITLE, PR_DESCRIPTION

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
  load_gh_token || return 1

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
    run_ai "$(prompt_pr_single_commit "$commit_subject" "$commit_body" "$changed_files")"
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

  run_ai "$(prompt_pr_multi_commit "$branch" "$issue" "$commits" "$commit_count" "$changed_files")"

  # shellcheck disable=SC2016  # backticks in single-quoted sed pattern are literal, not shell expansions
  PR_TITLE=$(echo "$AI_RESPONSE" | grep "^$PR_TITLE_MARKER" | sed "s/^$PR_TITLE_MARKER //" | head -1 | tr -d '\n\r' | sed 's/^`//;s/`$//')
  PR_DESCRIPTION=$(echo "$AI_RESPONSE" | sed -n "/^$PR_DESCRIPTION_MARKER/,$ p" | sed '1d' | sed 's/^```markdown$//' | sed 's/^```$//')

  PR_TITLE="${PR_TITLE:-feat: improve codebase}"
  if [ -z "$PR_DESCRIPTION" ]; then
    PR_DESCRIPTION=$(printf '## Summary\n\nBranch: %s\nCommits: %s' "$branch" "$commit_count")
  fi
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
