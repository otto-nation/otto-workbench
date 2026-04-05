#!/usr/bin/env bash
# Commit message generation and validation helpers.
# Requires lib/ai/core.sh to be sourced first.
# shellcheck source=compact_diff.sh
if [ -n "${BASH_SOURCE:-}" ]; then
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/compact_diff.sh"
else
  . "${TASKFILE_DIR:?commit.sh requires BASH_SOURCE or TASKFILE_DIR}/lib/ai/compact_diff.sh"
fi
#
# Typical call sequence:
#   find_commitlint_config   → sets COMMITLINT_CONFIG
#   build_commit_rules       → sets COMMIT_RULES (derived from COMMITLINT_CONFIG)
#   generate_commit_msg DIFF → sets AI_MSG
#   validate_commit_msg MSG  → validates; returns 1 on failure
#
# State set by functions: COMMITLINT_CONFIG, COMMIT_RULES, AI_MSG

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
    # shellcheck disable=SC2034  # COMMIT_RULES is read by prompt_commit in prompts.sh
    COMMIT_RULES="Follow the rules in this commitlint configuration: $(cat "$COMMITLINT_CONFIG")"
  else
    # Build a comma-separated display string from the space-separated COMMIT_TYPES constant
    local types_display
    types_display=$(echo "$COMMIT_TYPES" | tr ' ' ',')
    # shellcheck disable=SC2034  # COMMIT_RULES is read by prompt_commit in prompts.sh
    COMMIT_RULES="Follow these conventional commit rules:
- Use conventional commit format: type(scope): description
- Types: $types_display
- No period at end of subject
- Use semicolon (;) to separate multiple changes in header
- Separate header and body with blank line
- Use bullet points for multiple changes in body"
  fi
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

  run_ai "$(prompt_commit "$diff_content" "$files_section" "$retry_preamble")"
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
    local retry_preamble
    retry_preamble=$(prompt_commit_retry "$header" "$header_len" "$(( header_len - COMMIT_HEADER_MAX_LEN ))" "$prefix" "$subject_budget")
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
        echo "✗ Could not generate a valid commit message after 2 attempts."
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
