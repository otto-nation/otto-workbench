#!/usr/bin/env bash
# PR content generation: title, description, issue linking, template loading.
#
# Requires [`ai/core.sh`](#aicoresh) to be sourced first. Typical call sequence:
#
# ```bash
# load_pr [ARGS]                     # sets SKIP_ISSUE, PR_BASE, AI_COMMAND, BRANCH, DEFAULT_BRANCH
# push_branch BRANCH                 # pushes the branch if needed
# generate_pr_content BRANCH DEFAULT # sets PR_TITLE, PR_DESCRIPTION
# create_pr GH_ARGS...               # runs gh pr create, reports the PR URL
# ```
#
# State set by its functions: `BRANCH`, `DEFAULT_BRANCH`, `SKIP_ISSUE`,
# `PR_BASE`, `PR_ISSUE`, `PR_CLOSES`, `PR_TEMPLATE`, `PR_HAS_TEMPLATE`,
# `PR_TITLE`, `PR_DESCRIPTION`.

# _push_verified BRANCH [--set-upstream]
# Pushes BRANCH through the owner in ai/lib/git/push.py, which confirms the remote
# ref actually moved. Returns non-zero on a refused, lost, or unverified push,
# having already reported which of the three it was — nothing is echoed here,
# because a second "Push failed" would say it worse and say it twice.
_push_verified() {
  local branch="$1"; shift
  # push.py's own sibling imports (`from git import client`, `from core import
  # log`) resolve against ai/lib, not against ai/lib/git where the file now
  # lives — the package move deepened it by one directory, so the interpreter's
  # automatic sys.path[0] (the script's own directory) is no longer enough.
  PYTHONPATH="$WORKBENCH_ROOT/ai/lib${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$WORKBENCH_ROOT/ai/lib/git/push.py" \
    --cwd . --branch "$branch" --remote "$GIT_REMOTE" "$@"
}

# push_branch BRANCH
# Pushes BRANCH to remote, handling first-push and divergence cases.
# Returns 1 on any failure that should abort the caller.
push_branch() {
  local branch="$1"

  if ! git ls-remote --heads "$GIT_REMOTE" "$branch" | grep -q "$branch"; then
    echo "→ Pushing new branch to remote..."
    _push_verified "$branch" --set-upstream || return 1
    return 0
  fi

  git fetch "$GIT_REMOTE" "$branch" --quiet

  # If no tracking branch configured, just push
  if ! git rev-parse --verify "@{u}" &>/dev/null 2>&1; then
    _push_verified "$branch" || return 1
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
    _push_verified "$branch" || return 1
  else
    echo "✗ Branch has diverged from remote"
    echo "→ Fix with: git pull --rebase or git reset"
    return 1
  fi
}

# Matches only a GitHub pull request URL. Anchored to the /pull/<number> path so
# that non-PR URLs in gh's own output — such as the https://githubstatus.com link
# in its connectivity error — are never mistaken for a created PR.
PR_URL_PATTERN='https://github\.com/[^[:space:]]+/pull/[0-9]+'

# create_pr GH_ARGS...
# Runs `gh pr create` with the given arguments and reports the resulting PR URL.
# gh's exit code is the authoritative success signal; a zero exit with no parsable
# pull request URL is still treated as a failure. Returns 1 on any failure.
create_pr() {
  local output url status=0
  output=$(gh pr create "$@" 2>&1) || status=$?

  if [[ "$status" -ne 0 ]]; then
    echo "✗ PR creation failed (gh exited $status)"
    echo "$output"
    return 1
  fi

  url=$(printf '%s\n' "$output" | grep -oE "$PR_URL_PATTERN" | head -1 || true)
  if [[ -z "$url" ]]; then
    echo "✗ PR creation failed — gh reported success but printed no pull request URL"
    echo "$output"
    return 1
  fi

  echo "✓ Pull request created"
  echo "$url"
}

# _ensure_gh_repo_access
# Verifies the current gh auth can access the repo. If the automation token
# (GH_TOKEN) lacks access, unsets it to fall back to interactive gh auth.
# Must be called after load_gh_token. Returns 1 when neither auth method works.
_ensure_gh_repo_access() {
  # If no automation token is set, nothing to fall back from
  [ -n "${GH_TOKEN:-}" ] || return 0

  # Quick check: can the token see this repo?
  if gh repo view --json name -q .name &>/dev/null; then
    return 0
  fi

  echo "⚠  Automation token cannot access this repo — falling back to interactive gh auth"
  unset GH_TOKEN

  if ! gh auth status &>/dev/null; then
    echo "✗ Interactive gh auth also not available — run: gh auth login"
    return 1
  fi
}

# load_pr_context
# Loads the AI command, resolves the current branch context and verifies the
# effective base has a remote-tracking ref. Sets BRANCH and DEFAULT_BRANCH.
# Returns 1 on failure.
#
# The effective base is PR_BASE when the caller passed an explicit --base,
# otherwise the resolved default branch. resolve_default_branch can fall back to
# a guessed name that doesn't exist locally, and an explicit --base is exactly
# the escape hatch for that case, so it must not be refused on the guess's behalf.
#
# Must be called after parse_pr_flags (so PR_BASE is populated) and before
# generate_pr_content or push_branch — load_pr does both in order.
load_pr_context() {
  load_ai_command || return 1
  load_gh_token || return 1
  _ensure_gh_repo_access || return 1

  if ! gh auth status &>/dev/null; then
    echo "✗ GitHub CLI is not authenticated — run: gh auth login"
    return 1
  fi

  BRANCH=$(git branch --show-current)
  # See resolve_default_branch in lib/git_remote.sh for why this isn't rev-parse --abbrev-ref.
  DEFAULT_BRANCH=$(resolve_default_branch)

  # Keyed on DEFAULT_BRANCH, not the effective base below: this guards against running PR
  # operations while checked out on the repo's own default branch, which is nonsensical
  # regardless of what base the PR would target.
  if [ "$BRANCH" = "$DEFAULT_BRANCH" ]; then
    echo "✗ PR operations cannot be run from the $DEFAULT_BRANCH branch"
    return 1
  fi

  local target_base="${PR_BASE:-$DEFAULT_BRANCH}"
  if ! remote_branch_ref_exists "$target_base"; then
    echo "✗ $GIT_REMOTE/$target_base does not resolve — cannot open a PR against a base that doesn't exist"
    echo "→ Fix with: git fetch $GIT_REMOTE"
    if [ -z "${PR_BASE:-}" ]; then
      echo "→ If $DEFAULT_BRANCH is a guess and the real default branch differs, also run: git remote set-head $GIT_REMOTE -a"
    fi
    return 1
  fi
}

# _set_pr_flag FLAG VALUE
# Assigns VALUE to the variable corresponding to FLAG.
# shellcheck disable=SC2034  # PR_BASE, PR_TITLE_OVERRIDE, PR_BODY_OVERRIDE, PR_ISSUE_OVERRIDE read by callers
_set_pr_flag() {
  case "$1" in
    --base)      PR_BASE="$2" ;;
    --title)     PR_TITLE_OVERRIDE="$2" ;;
    --body)      PR_BODY_OVERRIDE="$2" ;;
    --body-file) PR_BODY_OVERRIDE="$(cat "$2")" ;;
    --issue)     PR_ISSUE_OVERRIDE="$2" ;;
    --closes)    _pr_add_close_ref "$2" || return 1 ;;
  esac
}

# parse_pr_flags ARGS
# Parses PR-specific flags from the CLI_ARGS string. Sets SKIP_ISSUE, PR_DRAFT,
# PR_BASE, PR_TITLE_OVERRIDE, PR_BODY_OVERRIDE, PR_CLOSES. Returns 1 on unknown
# flag, missing value, or an unclosable --closes reference.
#
# Uses eval to re-parse so quoted multi-word values work:
#   --title "fix: clean empty markers" --body-file /tmp/body.txt
parse_pr_flags() {
  local args="$1"
  SKIP_ISSUE=false
  # shellcheck disable=SC2034  # PR_DRAFT read by Taskfile callers
  PR_DRAFT=false
  # shellcheck disable=SC2034  # PR_BASE read by Taskfile callers
  PR_BASE=""
  PR_TITLE_OVERRIDE=""
  PR_BODY_OVERRIDE=""
  PR_ISSUE_OVERRIDE=""
  # shellcheck disable=SC2034  # PR_CLOSES read by _pr_append_issue_link
  PR_CLOSES=()

  [[ -z "$args" ]] && return 0

  local parsed=()
  # Re-parse with shell quoting rules so "multi word" values stay together
  eval "parsed=($args)" 2>/dev/null || read -ra parsed <<< "$args"

  local arg expect_flag=""
  for arg in "${parsed[@]}"; do
    if [[ -n "$expect_flag" ]]; then
      _set_pr_flag "$expect_flag" "$arg" || return 1
      expect_flag=""
      continue
    fi
    # shellcheck disable=SC2034  # PR_DRAFT is read by Taskfile callers
    case "$arg" in
      # Accepted and inert. Every documented pr:create invocation passes it to
      # suppress an issue prompt that no longer exists.
      --no-issue) SKIP_ISSUE=true ;;
      --draft)    PR_DRAFT=true ;;
      --issue)    expect_flag="$arg" ;;
      --base|--title|--body|--body-file|--closes) expect_flag="$arg" ;;
      *) printf "✗ Unknown flag: %s\n" "$arg"; return 1 ;;
    esac
  done
  if [[ -n "$expect_flag" ]]; then
    printf "✗ %s requires a value\n" "$expect_flag"
    return 1
  fi
}

# load_pr [ARGS]
# Parses PR flags from ARGS, then loads the PR context.
# Sets SKIP_ISSUE, PR_BASE, AI_COMMAND, BRANCH, DEFAULT_BRANCH. Returns 1 on failure.
load_pr() {
  local args="${1:-}"
  parse_pr_flags "$args" || return 1
  load_pr_context || return 1
}

# _pr_resolve_issue BRANCH
# Extracts an issue number from the branch name (e.g. feat/PROJ-42-desc → PROJ-42).
# When none is found, PR_ISSUE is left empty and the AI prompt renders it as
# "Issue: None" — nothing is read from stdin. This ran an interactive prompt
# once, which aborted the task outright whenever stdin was not a terminal.
# The issue here is context for the generated description; a PR that should
# close something says so with --closes.
# Sets PR_ISSUE.
_pr_resolve_issue() {
  local branch="$1"

  if [[ -n "${PR_ISSUE_OVERRIDE:-}" ]]; then
    PR_ISSUE="$PR_ISSUE_OVERRIDE"
    echo "✓ Using issue: $PR_ISSUE"
    return
  fi

  PR_ISSUE=$(echo "$branch" | grep -oE '[A-Z]+-[0-9]+' | head -1)

  if [ -n "$PR_ISSUE" ]; then
    echo "✓ Found issue number: $PR_ISSUE"
  fi
  return 0
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
    run_ai "$(prompt_pr_single_commit "$commit_subject" "$commit_body" "$changed_files")" "" "pr-description"
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

  run_ai "$(prompt_pr_multi_commit "$branch" "$issue" "$commits" "$commit_count" "$changed_files")" "" "pr-description"

  # shellcheck disable=SC2016  # backticks in single-quoted sed pattern are literal, not shell expansions
  PR_TITLE=$(echo "$AI_RESPONSE" | grep "^$PR_TITLE_MARKER" | sed "s/^$PR_TITLE_MARKER //" | head -1 | tr -d '\n\r' | sed 's/^`//;s/`$//')
  PR_DESCRIPTION=$(echo "$AI_RESPONSE" | sed -n "/^$PR_DESCRIPTION_MARKER/,$ p" | sed '1d' | sed 's/^```markdown$//' | sed 's/^```$//')

  PR_TITLE="${PR_TITLE:-feat: improve codebase}"
  if [ -z "$PR_DESCRIPTION" ]; then
    PR_DESCRIPTION=$(printf '## Summary\n\nBranch: %s\nCommits: %s' "$branch" "$commit_count")
  fi
}

# _pr_issue_provider
# The repo's issues.provider, or nothing when no scope sets one.
#
# wb_config_get in lib/config.sh is the documented reader and cannot be used
# here: it needs the constants block, and lib/constants.sh resolves its own
# directory from BASH_SOURCE, which go-task's shell leaves unset — sourcing it
# from a task block fails and leaves the guard tripping. The record format is
# three tab-separated fields, documented in lib/config_cli.py. The key is
# spelled out rather than read from ISSUE_PROVIDER_CONFIG_KEY for the same
# reason; tests/config.bats cross-validates that constant against Python.
_pr_issue_provider() {
  local record
  record="$(python3 "$WORKBENCH_ROOT/lib/config_cli.py" get issues.provider 2>/dev/null)" || return 0
  printf '%s' "$record" | cut -f2
  return 0
}

# _pr_add_close_ref ID
# Appends one validated closing reference to PR_CLOSES. Accepts a GitHub issue
# number (941 or #941) anywhere, and a tracker key (ENG-123) only where it can
# actually close something. Returns 1, having said why, on a reference nothing
# can close — a bad --closes is refused before the PR is opened rather than
# becoming a dead link in a body that is already published.
_pr_add_close_ref() {
  local raw="${1##\#}" provider

  case "$raw" in
    ''|*[!0-9]*) ;;
    *) PR_CLOSES+=("#$raw"); return 0 ;;
  esac

  # Anchored, and matched with grep rather than a case glob: a glob's `*` would
  # admit anything between the letters and the digits, and what it admitted
  # would reach _pr_close_ref_present as part of a regex.
  if printf '%s' "$raw" | grep -qE '^[A-Z]+-[0-9]+$'; then
    provider="$(_pr_issue_provider)"
    if [ "$provider" != "linear" ]; then
      printf "✗ --closes %s: a tracker key only auto-closes on Linear, and issues.provider is '%s'\n" \
        "$raw" "${provider:-unset}"
      return 1
    fi
    PR_CLOSES+=("$raw")
    return 0
  fi

  printf "✗ --closes %s: expected a GitHub issue number (941 or #941) or a tracker key (ENG-123)\n" "$raw"
  return 1
}

# _pr_close_ref_present BODY REF
# Whether BODY already closes REF under any of GitHub's closing keywords. The
# trailing ([^0-9]|$) is what keeps "#1" from matching a body that closes "#12".
_pr_close_ref_present() {
  printf '%s' "$1" | grep -qiE "(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]+$2([^0-9]|\$)"
}

# _pr_append_issue_link
# Appends one "Closes <ref>" line per entry in PR_CLOSES that PR_DESCRIPTION
# does not already carry, after a blank line at the end of the body.
#
# Appended rather than prepended because a templated body's section headers are
# a contract — content above the first heading, or injected into a section the
# AI wrote, is content the template did not ask for. GitHub honours a closing
# keyword anywhere in the body, so the end costs nothing, and re-running
# pr:update over a body that already links is then a no-op.
#
# Reads and modifies PR_DESCRIPTION in place. Returns 0 on every path: it is the
# last statement of generate_pr_content, and go-task aborts a task on a non-zero
# command.
_pr_append_issue_link() {
  if [ "${#PR_CLOSES[@]}" -eq 0 ]; then
    return 0
  fi

  local pending="" ref
  for ref in "${PR_CLOSES[@]}"; do
    if _pr_close_ref_present "$PR_DESCRIPTION" "$ref"; then
      echo "✓ Already linked: Closes $ref"
      continue
    fi
    pending="${pending}Closes ${ref}"$'\n'
  done

  if [ -z "$pending" ]; then
    return 0
  fi

  # The command substitution strips trailing newlines, so the blank line below
  # is exactly one however the generated body happened to end.
  PR_DESCRIPTION="$(printf '%s' "$PR_DESCRIPTION")"$'\n\n'"${pending%$'\n'}"
  echo "✓ Linked for auto-close on merge: ${pending//$'\n'/ }"
  return 0
}

# pr_preserve_close_refs OLD_BODY
# Re-appends to PR_DESCRIPTION any closing reference OLD_BODY carried that the
# regenerated body lost. For pr:update, where `gh pr edit --body` replaces the
# published body outright: an issue somebody linked on the PR stays linked
# across a regeneration it had no part in.
#
# Modifies PR_DESCRIPTION in place. Returns 0 on every path.
pr_preserve_close_refs() {
  local old_body="$1" ref
  [ -n "$old_body" ] || return 0

  # One ref per line, deduplicated, keyword and case normalised away.
  local found
  found=$(printf '%s' "$old_body" \
    | grep -oiE '(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]+(#[0-9]+|[A-Z]+-[0-9]+)' \
    | grep -oE '(#[0-9]+|[A-Z]+-[0-9]+)$' \
    | sort -u || true)
  [ -n "$found" ] || return 0

  local restored=""
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    _pr_close_ref_present "$PR_DESCRIPTION" "$ref" && continue
    PR_DESCRIPTION="$(printf '%s' "$PR_DESCRIPTION")"$'\n\n'"Closes $ref"
    restored="$restored $ref"
  done <<< "$found"

  [ -n "$restored" ] && echo "✓ Preserved existing issue link(s):$restored"
  return 0
}
}

# generate_pr_content BRANCH DEFAULT_BRANCH
# Requires AI_COMMAND (unless PR_TITLE_OVERRIDE and PR_BODY_OVERRIDE are set).
# Sets PR_TITLE and PR_DESCRIPTION.
generate_pr_content() {
  local branch="$1"
  local default_branch="$2"

  if [[ -n "${PR_TITLE_OVERRIDE:-}" && -n "${PR_BODY_OVERRIDE:-}" ]]; then
    PR_TITLE="$PR_TITLE_OVERRIDE"
    PR_DESCRIPTION="$PR_BODY_OVERRIDE"
    _pr_append_issue_link
    return 0
  fi

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

  [[ -n "${PR_TITLE_OVERRIDE:-}" ]] && PR_TITLE="$PR_TITLE_OVERRIDE"
  [[ -n "${PR_BODY_OVERRIDE:-}" ]] && PR_DESCRIPTION="$PR_BODY_OVERRIDE"

  _pr_append_issue_link
}
