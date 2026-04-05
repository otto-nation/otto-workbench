#!/usr/bin/env bash
# AI prompt templates for git automation.
# Requires lib/ai/core.sh to be sourced first (for COMMIT_* and PR_* constants).
#
# Each function prints a filled prompt to stdout — no logic, pure text.
# Callers pass dynamic values as arguments; configuration globals (COMMIT_RULES,
# PR_TEMPLATE, COMMIT_HEADER_MAX_LEN, etc.) are read directly from core.sh.
#
# Functions:
#   prompt_commit DIFF FILES_SECTION        — commit message generation
#   prompt_commit_retry HEADER LEN OVER     — retry preamble when header is too long
#                       PREFIX BUDGET
#   prompt_pr_single_commit SUBJECT BODY    — PR description for single-commit branches
#                           CHANGED_FILES
#   prompt_pr_multi_commit BRANCH ISSUE     — PR title + description for multi-commit branches
#                          COMMITS COUNT
#                          CHANGED_FILES
#   prompt_diff_review CONTEXT              — review staged/unstaged/committed changes
#   prompt_pr_review PR_NUMBER TITLE        — review an existing PR
#                    BODY DIFF

# prompt_commit DIFF_CONTENT FILES_SECTION [RETRY_PREAMBLE]
# Generates the commit message prompt. When RETRY_PREAMBLE is provided it is
# prepended with a blank line separator so the AI sees the failure context first.
prompt_commit() {
  local diff_content="$1" files_section="$2" retry_preamble="${3:-}"

  if [ -n "$retry_preamble" ]; then
    printf '%s\n\n' "$retry_preamble"
  fi

  cat <<EOF
Generate a conventional commit message based on the changes.

CRITICAL REQUIREMENTS:
- Header MUST be ≤${COMMIT_HEADER_MAX_LEN} characters total
- Header = type + optional "(scope)" + ": " + subject
- Subject budget = ${COMMIT_HEADER_MAX_LEN} minus your prefix length
  Example: "feat(auth): " is 12 chars -> subject must be <=60 chars
  Example: "fix: " is 5 chars -> subject must be <=67 chars
  Example: "refactor(payments): " is 20 chars -> subject must be <=52 chars
- Before writing, count your prefix length, subtract from ${COMMIT_HEADER_MAX_LEN}, then write a subject within that budget
- Each body line MUST be ≤${COMMIT_BODY_MAX_LEN} characters (wrap long lines)
- Subject must be concise — focus on WHAT changed, not HOW
- If multiple changes, use semicolon in subject or list in body

${COMMIT_RULES}

${files_section}Diff:
${diff_content}

Return only the raw commit message text. No markdown, no code blocks, no backticks, no explanation.
EOF
}

# prompt_commit_retry HEADER HEADER_LEN OVER PREFIX SUBJECT_BUDGET
# Outputs a retry preamble that gives the AI the exact character budget it needs.
# Passed as RETRY_PREAMBLE to a second call of prompt_commit.
prompt_commit_retry() {
  local header="$1" header_len="$2" over="$3" prefix="$4" subject_budget="$5"

  cat <<EOF
PREVIOUS ATTEMPT FAILED: '${header}' is ${header_len} characters — ${over} over the limit.

You used the prefix '${prefix}' (${#prefix} chars). That leaves EXACTLY ${subject_budget} characters for the subject. Write a subject of ${subject_budget} characters or fewer. Count every character. Use the same prefix unless it genuinely does not fit.
EOF
}

# prompt_pr_single_commit COMMIT_SUBJECT COMMIT_BODY CHANGED_FILES
# For single-commit branches where a PR template exists: asks the AI to fill
# the template using the commit message. Reads PR_TEMPLATE global.
prompt_pr_single_commit() {
  local commit_subject="$1" commit_body="$2" changed_files="$3"

  cat <<EOF
Fill out this PR template based on the commit below. Return only the filled template body — no title, no markers, no extra commentary.

Template:
${PR_TEMPLATE}

Commit subject: ${commit_subject}
Commit body: ${commit_body:-<none>}

Changed files:
${changed_files}
EOF
}

# prompt_pr_multi_commit BRANCH ISSUE COMMITS COMMIT_COUNT CHANGED_FILES
# For multi-commit branches: asks the AI to generate a PR title and fill the
# template. Reads PR_TEMPLATE, PR_TITLE_MARKER, PR_DESCRIPTION_MARKER globals.
prompt_pr_multi_commit() {
  local branch="$1" issue="$2" commits="$3" commit_count="$4" changed_files="$5"

  cat <<EOF
Generate a professional PR title and fill out this template based on the changes:

Template:
${PR_TEMPLATE}

Branch: ${branch}
Issue: ${issue:-None}
Commits: ${commit_count}

Recent commits:
${commits}

Changed files:
${changed_files}

Return: ${PR_TITLE_MARKER} <title>
${PR_DESCRIPTION_MARKER} <filled template>
EOF
}

# prompt_diff_review CONTEXT
# CONTEXT is a pre-built string of labelled diff sections (committed, staged, unstaged).
# Built by generate_diff_review before calling this function.
prompt_diff_review() {
  local context="$1"

  cat <<EOF
Review the following code changes and provide actionable feedback.

Focus on:
- Bugs, logic errors, and edge cases
- Security vulnerabilities
- Performance concerns
- Code quality and maintainability
- Missing error handling
- Improvements worth making

Be concise and direct. Group feedback by section when relevant. Skip sections with no issues.

${context}

Provide a brief summary first, then specific findings.
EOF
}

# prompt_pr_review PR_NUMBER PR_TITLE PR_BODY COMPACT_DIFF
prompt_pr_review() {
  local pr_number="$1" pr_title="$2" pr_body="$3" compact_diff="$4"

  cat <<EOF
Review this pull request and provide actionable feedback.

PR #${pr_number}: ${pr_title}

Description:
${pr_body}

Focus on:
- Bugs, logic errors, and edge cases
- Security vulnerabilities
- Performance concerns
- Code quality and maintainability
- Missing error handling
- Whether the changes match the PR description
- Missing tests

Be concise and direct. Group findings by file or category. Skip areas with no issues.

Diff:
${compact_diff}

Provide a brief overall summary first, then specific findings.
EOF
}
