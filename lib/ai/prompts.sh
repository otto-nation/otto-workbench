#!/usr/bin/env bash
# Prompt templates for all AI automation — pure text generation, no side
# effects.
#
# Each function prints a filled prompt to stdout. Callers pass the dynamic values
# as arguments; the configuration globals (`COMMIT_RULES`,
# `COMMIT_HEADER_MAX_LEN`, and the rest) are read straight from
# [`ai/core.sh`](#aicoresh), which must be sourced first.

# prompt_commit DIFF_CONTENT FILES_SECTION [RETRY_PREAMBLE] [SURFACE_NOTE]
# Generates the commit message prompt. RETRY_PREAMBLE is prepended to it and
# SURFACE_NOTE is rendered directly after COMMIT_RULES, each when non-empty.
#
# The preamble is separated by a blank line so the AI sees the failure context
# first. The surface note sits alongside the existing breaking-change footer
# rule, so the model sees the footer instruction and the reason for it in the
# same place.
prompt_commit() {
  local diff_content="$1" files_section="$2" retry_preamble="${3:-}" surface_note="${4:-}"

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

${COMMIT_RULES}${surface_note}

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

# prompt_diff_review CONTEXT
# CONTEXT is a pre-built string of labelled diff sections (committed, staged, unstaged).
# Built by generate_diff_review before calling this function.
# Review instructions come from the reviewer agent — this prompt provides data only.
prompt_diff_review() {
  local context="$1"

  cat <<EOF
Review the following code changes.

${context}
EOF
}

# prompt_pr_review PR_NUMBER PR_TITLE PR_BODY COMPACT_DIFF
# Review instructions come from the reviewer agent — this prompt provides data only.
prompt_pr_review() {
  local pr_number="$1" pr_title="$2" pr_body="$3" compact_diff="$4"

  cat <<EOF
Review this pull request.

PR #${pr_number}: ${pr_title}

Description:
${pr_body}

Diff:
${compact_diff}
EOF
}
