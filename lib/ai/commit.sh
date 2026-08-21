#!/usr/bin/env bash
# Commit message generation with validation and automatic retry on length
# violations.
#
# Requires [`ai/core.sh`](#aicoresh) to be sourced first. Typical call sequence:
#
# ```bash
# find_commitlint_config   # sets COMMITLINT_CONFIG
# build_commit_rules       # sets COMMIT_RULES (derived from COMMITLINT_CONFIG)
# generate_commit_msg DIFF # sets AI_MSG
# validate_commit_msg MSG  # validates; returns 1 on failure
# ```
#
# State set by its functions: `COMMITLINT_CONFIG`, `COMMIT_RULES`, `AI_MSG`.

# shellcheck source=compact_diff.sh
if [ -n "${BASH_SOURCE:-}" ]; then
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/compact_diff.sh"
else
  . "${TASKFILE_DIR:?commit.sh requires BASH_SOURCE or TASKFILE_DIR}/lib/ai/compact_diff.sh"
fi

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
#
# These rules are static and go to every commit in every repo that sources this
# library, so they name the breaking-change footer but not $NOT_BREAKING_FOOTER.
# That footer is only ever correct against a concrete list of removed entries —
# without one the model has nothing to put after 'Not-Breaking:' and would be
# invited to invent an entry name the gate then reads as undeclared. It is
# taught by _surface_note instead, which fires only when the gate has named the
# entries the footer would have to cover.
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
- Use bullet points for multiple changes in body
- If the change removes or renames anything on the public surface, add a
  '$BREAKING_CHANGE_FOOTER: <what broke>' footer as the last paragraph of the body
- The '!' marker (e.g. feat!:) may be used in the header but never replaces the
  footer — squash merges can discard the subject, the body always survives"
  fi
}

# _surface_removals REPO_DIR
# Prints one removed public-surface entry per line, empty when the surface is
# intact. Silent when the gate is missing or non-executable — an unavailable
# gate must not block message generation, it only forfeits the extra prompt
# context. When the gate runs but exits with anything other than 0 (compatible)
# or 1 (undeclared removals) — a jq/data error (5), a bad snapshot blob (2), or
# an unresolvable merge base (128) — the check never ran to completion, so any
# REMOVED lines printed before the abort are a partial result and are
# discarded. That is announced on stderr rather than passed over in silence,
# since "never swallow errors silently" applies here too even though it does
# not block the commit. Only the status is reported: the gate's own stderr is
# dropped because on exit 1, its normal case here, it is the full contributor-
# facing rejection report, and printing that while the message is still being
# drafted would announce a push failure that has not happened.
# WORKBENCH_SURFACE_GATE overrides the gate path — a test-only seam, not a
# taskfile.env setting a contributor is expected to set.
_surface_removals() {
  local repo_dir="$1"
  local gate="${WORKBENCH_SURFACE_GATE:-$repo_dir/bin/local/check-surface-compat}"
  [[ -x "$gate" ]] || return 0

  local gate_output gate_status=0
  gate_output=$("$gate" --repo-dir "$repo_dir" --quiet 2>/dev/null) || gate_status=$?

  if [[ "$gate_status" -ne 0 && "$gate_status" -ne 1 ]]; then
    echo "→ Surface gate exited $gate_status — skipping the removed-surface prompt hint" >&2
    return 0
  fi
  sed -n 's/^REMOVED //p' <<<"$gate_output"
}

# _surface_note REPO_DIR
# Prints the prompt paragraph naming the public-surface entries this change
# removes, or nothing when the surface is intact.
_surface_note() {
  local removals
  removals=$(_surface_removals "$1")
  [[ -n "$removals" ]] || return 0

  # Heredoc delimiter is deliberately unquoted (<<EOF, not <<'EOF') so
  # $BREAKING_CHANGE_FOOTER, $NOT_BREAKING_FOOTER, and the $(sed ...) command
  # substitution below all expand — quoting the delimiter would silently
  # print the literal variable names instead.
  #
  # The sed stays rather than becoming lib/output.sh's `indent`: go-task sources
  # this half through `sh -c`, so lib/ai/ deliberately reaches nothing behind
  # lib/ui.sh. ShellCheck's SC2001 suggestion does not apply either — parameter
  # expansion has no line anchor to replace `^` with.
  # shellcheck disable=SC2001
  cat <<EOF


This change removes the following entries from the package's public surface:
$(sed 's/^/  /' <<<"$removals")

If this is a breaking change, the body MUST end with a
'$BREAKING_CHANGE_FOOTER: <what broke>' footer naming what a user has to change.
If it genuinely is not breaking (e.g. a rename that ships a back-compat alias),
add one '$NOT_BREAKING_FOOTER: <entry> — <reason>' footer per removed entry
instead — use the entry name exactly as it appears above, with no leading dash
or bullet marker.
EOF
}

# _build_commit_prompt DIFF FILES_SECTION SURFACE_NOTE [RETRY_PREAMBLE]
# Internal helper. Builds and runs the AI prompt; sets AI_MSG.
_build_commit_prompt() {
  local diff_content="$1"
  local files_section="$2"
  local surface_note="$3"
  local retry_preamble="${4:-}"

  # When the diff exceeds the budget, include as many complete per-file diffs
  # as fit (smallest files first) so the AI always sees whole-file context.
  if [ "${#diff_content}" -gt "$DIFF_MAX_CHARS" ]; then
    diff_content=$(_compact_diff "$diff_content")
  fi

  run_ai "$(prompt_commit "$diff_content" "$files_section" "$retry_preamble" "$surface_note")" "" "commit-message"
  AI_MSG="$AI_RESPONSE"
}

# generate_commit_msg DIFF [FILE_LIST]
# Requires AI_COMMAND and COMMIT_RULES. Sets AI_MSG. Retries once with a precise
# character budget if the header exceeds COMMIT_HEADER_MAX_LEN, and returns 1 if
# the retry also fails.
#
# LLMs cannot reliably count characters, so the caller should surface that
# failure rather than proceeding with an invalid message.
generate_commit_msg() {
  local diff_content="$1"
  local file_list="${2:-}"
  local files_section=""

  if [ -n "$file_list" ]; then
    files_section="Files changed: $file_list

"
  fi

  # Resolved once and threaded into both attempts. The gate shells out to git
  # and jq over two snapshots, and the header-retry path below would otherwise
  # pay for all of it a second time to arrive at the same paragraph.
  #
  # The repo to check is the caller's, which for a library the workbench ships
  # and any repo can source is not the repo this file lives in — so the root
  # comes from git, not from this file's own BASH_SOURCE. A caller with GIT_DIR
  # exported (a git hook) gets its cwd instead of the repo root; that finds no
  # gate and drops the hint, which is the same outcome as any repo that does
  # not ship one, and `task commit` is not invoked from a hook.
  local surface_note
  surface_note=$(_surface_note "$(git rev-parse --show-toplevel 2>/dev/null)")

  _build_commit_prompt "$diff_content" "$files_section" "$surface_note"

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
    _build_commit_prompt "$diff_content" "$files_section" "$surface_note" "$retry_preamble"

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
    local commit_pattern="^(${types_regex})(\(.+\))?!?: .+"
    if ! echo "$header" | grep -qE "$commit_pattern"; then
      echo "✗ Header does not follow conventional commit format: $header"
      echo "  Expected: type(scope): description"
      return 1
    fi
    # A `!` header claims a breaking change, but the footer is what carries it:
    # this repo squash-merges with COMMIT_OR_PR_TITLE, so on a multi-commit PR
    # the PR title replaces the subject and the marker never reaches
    # release-please. bin/local/check-surface-compat rejects the same message at
    # push time; accepting it here only defers the rejection by a few minutes.
    local bang_pattern="^(${types_regex})(\(.+\))?!: "
    if echo "$header" | grep -qE "$bang_pattern" && ! has_breaking_footer "$msg"; then
      echo "✗ Header claims a breaking change but the body has no ${BREAKING_CHANGE_FOOTER} footer: $header"
      echo "  Squash merges discard the subject — add to the body:"
      echo "    ${BREAKING_CHANGE_FOOTER}: <what broke>"
      return 1
    fi
  fi
}

# _footer_key LINE
# Reduces a declared footer line to a dedup key: the footer type, plus for
# Not-Breaking the surface entry it names — the reason is dropped.
#
# Two footers that declare the same fact must collide even when their reason
# text differs, which is exactly what happens when the reword's regenerated
# message earns its own breaking-change footer from the same diff: the
# model's wording will not match the original's byte for byte, so a whole-line
# dedup would keep both. Mirrors the slice _declared_keys in
# bin/local/check-surface-compat computes for Not-Breaking, for the same
# reason: the entry is the identity, the reason is not.
_footer_key() {
  local line="$1" rest
  if [[ "$line" =~ ^($BREAKING_CHANGE_FOOTER|$BREAKING_CHANGE_FOOTER_ALT):\ .+$ ]]; then
    printf '%s' "$BREAKING_CHANGE_FOOTER"
    return
  fi
  if [[ "$line" =~ ^$NOT_BREAKING_FOOTER:\ (.+)$ ]]; then
    rest="${BASH_REMATCH[1]}"
    if [[ "$rest" =~ ^(.+)[[:space:]](—|–|-+)[[:space:]] ]]; then
      printf '%s: %s' "$NOT_BREAKING_FOOTER" "${BASH_REMATCH[1]}"
      return
    fi
  fi
  printf '%s' "$line"
}

# preserve_declared_footers ORIGINAL_MSG
# Re-appends to AI_MSG every declaration footer ORIGINAL_MSG carries that the
# generated message does not already have.
#
# A reword regenerates the message from the commit's diff alone, so a
# BREAKING CHANGE or Not-Breaking footer the author already wrote is dropped
# wholesale. The surface gate cannot catch that: it scans every commit on the
# branch, and the footer about to be reworded away is still there while it runs.
#
# Carried mechanically rather than handed to the model as context to preserve —
# the reason text has to reach git history byte for byte, which is the entire
# argument for a footer over a checked-in allowlist, and a model paraphrases.
preserve_declared_footers() {
  local original="$1"
  local footers line key ai_line ai_key
  local missing=()
  footers=$(declared_footers "$original")

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    if printf '%s\n' "$AI_MSG" | grep -qxF "$line"; then
      continue
    fi

    # The regenerated message may already declare the same fact under
    # different wording (e.g. its own BREAKING CHANGE footer from the same
    # diff) — drop that copy so the original's human-authored reason is the
    # only one that survives, not both.
    key=$(_footer_key "$line")
    while IFS= read -r ai_line; do
      [[ -n "$ai_line" ]] || continue
      ai_key=$(_footer_key "$ai_line")
      if [[ "$ai_key" == "$key" ]]; then
        AI_MSG=$(printf '%s\n' "$AI_MSG" | grep -vxF "$ai_line")
      fi
    done <<<"$(declared_footers "$AI_MSG")"

    missing+=("$line")
  done <<<"$footers"

  [[ "${#missing[@]}" -gt 0 ]] || return 0

  local block
  block=$(printf '%s\n' "${missing[@]}")
  AI_MSG="$AI_MSG

$block"
}
