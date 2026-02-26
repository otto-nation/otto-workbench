#!/bin/bash
# Shared helpers for AI-powered commit message generation.
# Sourced by commit and reword tasks via: . "{{.TASKFILE_DIR}}/lib/ai-commit.sh"
#
# Requires AI_COMMAND to be set before calling generate_commit_msg.
# All other state (COMMITLINT_CONFIG, COMMIT_RULES, AI_MSG) is set by the functions below.

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
# Sets COMMIT_RULES.
build_commit_rules() {
  if [ -n "$COMMITLINT_CONFIG" ]; then
    COMMIT_RULES="Follow the rules in this commitlint configuration: $(cat "$COMMITLINT_CONFIG")"
  else
    COMMIT_RULES="Follow these conventional commit rules:
- Use conventional commit format: type(scope): description
- Types: feat, fix, perf, deps, revert, docs, style, refactor, test, build, ci, chore
- No period at end of subject
- Use semicolon (;) to separate multiple changes in header
- Separate header and body with blank line
- Use bullet points for multiple changes in body"
  fi
}

# generate_commit_msg DIFF [FILE_LIST]
# Requires AI_COMMAND and COMMIT_RULES.
# Sets AI_MSG.
generate_commit_msg() {
  local diff_content="$1"
  local file_list="${2:-}"
  local files_section=""

  if [ -n "$file_list" ]; then
    files_section="Files changed: $file_list

"
  fi

  local ai_prompt="Generate a conventional commit message based on the changes.

CRITICAL REQUIREMENTS:
- Header MUST be ≤72 characters total (type + scope + colon + space + subject)
- Each body line MUST be ≤100 characters (wrap long lines)
- Type is required; scope is optional
- Subject must be concise - focus on WHAT changed, not HOW
- If multiple changes, use semicolon in subject or list in body
- Count characters carefully before responding

$COMMIT_RULES

${files_section}Diff:
$diff_content

Return only the raw commit message text. No markdown, no code blocks, no backticks, no explanation."

  AI_MSG=$(echo "$ai_prompt" | $AI_COMMAND | tr -d '\033' | sed 's/\[[0-9;]*m//g' | sed 's/^[> ]*//g' | sed '/^```/d')
}

# validate_commit_msg MSG
# Requires COMMITLINT_CONFIG (set by find_commitlint_config).
# Skips silently if no config found or npx unavailable.
# Returns 1 on validation failure.
validate_commit_msg() {
  local msg="$1"
  if [ -z "$COMMITLINT_CONFIG" ] || ! command -v npx &>/dev/null; then
    return 0
  fi
  echo "→ Validating commit message..."
  if ! echo "$msg" | npx commitlint --config "$COMMITLINT_CONFIG" 2>&1; then
    echo "✗ Commit message failed commitlint validation"
    return 1
  fi
  echo "✓ Commit message validated"
  echo ""
}
