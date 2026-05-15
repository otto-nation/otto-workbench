#!/usr/bin/env bats
# Tests for claude-review helper functions — PR extraction, usage formatting,
# issue detection, and archive management.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  CLAUDE_REVIEW="$REPO_ROOT/ai/claude/bin/claude-review"

  # Source claude-review with side effects neutralized
  export HOME="$TMPDIR"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$CLAUDE_REVIEW"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a minimal session log with a "type":"result" record.
_make_session_log() {
  local dest="$1" cost="${2:-1.0}" input_tokens="${3:-100}" output_tokens="${4:-200}" duration_ms="${5:-60000}"
  local cache_read="${6:-0}" cache_create="${7:-0}"
  cat > "$dest" <<EOF
{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}
{"type":"result","subtype":"success","is_error":false,"duration_ms":${duration_ms},"total_cost_usd":${cost},"usage":{"input_tokens":${input_tokens},"output_tokens":${output_tokens},"cache_read_input_tokens":${cache_read},"cache_creation_input_tokens":${cache_create}}}
EOF
}

# ── _extract_pr_number ───────────────────────────────────────────────────────

@test "_extract_pr_number: full GitHub URL extracts number" {
  local result
  _extract_pr_number result "https://github.com/org/repo/pull/42"
  [ "$result" = "42" ]
}

@test "_extract_pr_number: bare number returns as-is" {
  local result
  _extract_pr_number result "123"
  [ "$result" = "123" ]
}

@test "_extract_pr_number: URL with trailing path segments" {
  local result
  _extract_pr_number result "https://github.com/org/repo/pull/99/files"
  [ "$result" = "99" ]
}

@test "_extract_pr_number: single digit number" {
  local result
  _extract_pr_number result "1"
  [ "$result" = "1" ]
}

@test "_extract_pr_number: invalid input exits non-zero" {
  run bash -c 'export HOME="$1"; NO_COLOR=1 source "$2" && _extract_pr_number result "not-a-number"' \
    -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Cannot extract PR number"* ]]
}

@test "_extract_pr_number: empty string exits non-zero" {
  run bash -c 'export HOME="$1"; NO_COLOR=1 source "$2" && _extract_pr_number result ""' \
    -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -ne 0 ]
}

# ── _review_file ─────────────────────────────────────────────────────────────

@test "_review_file: constructs path from org/repo and PR number" {
  local result
  _review_file result "org/my-repo" "42"
  [ "$result" = "$REVIEWS_DIR/my-repo-42.md" ]
}

@test "_review_file: repo name with hyphens preserved" {
  local result
  _review_file result "org/my-cool-repo" "1"
  [ "$result" = "$REVIEWS_DIR/my-cool-repo-1.md" ]
}

@test "_review_file: strips only last path component" {
  local result
  _review_file result "deep/nested/repo" "7"
  [ "$result" = "$REVIEWS_DIR/repo-7.md" ]
}

# ── _extract_issue_id ────────────────────────────────────────────────────────

@test "_extract_issue_id: linear provider extracts from branch" {
  local result
  _extract_issue_id result "linear" "feat/ABC-123-description"
  [ "$result" = "ABC-123" ]
}

@test "_extract_issue_id: linear provider falls back to PR body" {
  local result
  _extract_issue_id result "linear" "feat/no-issue-here" "Fixes ABC-456 in production"
  [ "$result" = "ABC-456" ]
}

@test "_extract_issue_id: linear provider returns empty when no match" {
  local result
  _extract_issue_id result "linear" "feat/no-issue" "no issue here either"
  [ -z "$result" ]
}

@test "_extract_issue_id: jira provider same pattern as linear" {
  local result
  _extract_issue_id result "jira" "fix/PROJ-789-bugfix"
  [ "$result" = "PROJ-789" ]
}

@test "_extract_issue_id: github provider extracts from Closes keyword" {
  local result
  _extract_issue_id result "github" "feat/something" "Closes #789"
  [ "$result" = "789" ]
}

@test "_extract_issue_id: github provider extracts from Fixes keyword" {
  local result
  _extract_issue_id result "github" "feat/something" "Fixes #12"
  [ "$result" = "12" ]
}

@test "_extract_issue_id: github provider extracts from Resolves keyword" {
  local result
  _extract_issue_id result "github" "feat/something" "resolves #1"
  [ "$result" = "1" ]
}

@test "_extract_issue_id: github provider returns empty without closing keyword" {
  local result
  _extract_issue_id result "github" "feat/something" "see issue #42 for details"
  [ -z "$result" ]
}

@test "_extract_issue_id: none provider always returns empty" {
  local result
  _extract_issue_id result "none" "feat/ABC-123-description" "Closes #42"
  [ -z "$result" ]
}

@test "_extract_issue_id: linear provider takes first match from branch" {
  local result
  _extract_issue_id result "linear" "feat/ABC-1-and-DEF-2"
  [ "$result" = "ABC-1" ]
}

# ── _format_usage ────────────────────────────────────────────────────────────

@test "_format_usage: single log file with one result" {
  _make_session_log "$TMPDIR/session.jsonl" 1.50 100 200 65000
  run _format_usage "$TMPDIR/session.jsonl"
  [ "$status" -eq 0 ]
  [[ "$output" == *'$1.50'* ]]
  [[ "$output" == *'300'* ]]
  [[ "$output" == *'1m 5s'* ]]
}

@test "_format_usage: multiple log files aggregates correctly" {
  _make_session_log "$TMPDIR/session1.jsonl" 1.00 100 200 60000
  _make_session_log "$TMPDIR/session2.jsonl" 2.00 300 400 120000
  run _format_usage "$TMPDIR/session1.jsonl" "$TMPDIR/session2.jsonl"
  [ "$status" -eq 0 ]
  [[ "$output" == *'$3.00'* ]]
  [[ "$output" == *'1k'* ]]
  [[ "$output" == *'3m 0s'* ]]
}

@test "_format_usage: no result lines returns silently" {
  echo '{"type":"assistant","message":{}}' > "$TMPDIR/no-result.jsonl"
  run _format_usage "$TMPDIR/no-result.jsonl"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_format_usage: empty file returns silently" {
  touch "$TMPDIR/empty.jsonl"
  run _format_usage "$TMPDIR/empty.jsonl"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_format_usage: non-existent file returns silently" {
  run _format_usage "$TMPDIR/does-not-exist.jsonl"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_format_usage: mix of existing and non-existing files" {
  _make_session_log "$TMPDIR/real.jsonl" 2.50 500 500 30000
  run _format_usage "$TMPDIR/real.jsonl" "$TMPDIR/missing.jsonl"
  [ "$status" -eq 0 ]
  [[ "$output" == *'$2.50'* ]]
  [[ "$output" == *'1k'* ]]
}

@test "_format_usage: no args returns silently" {
  run _format_usage
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_format_usage: token formatting — under 1k shows raw" {
  _make_session_log "$TMPDIR/small.jsonl" 0.10 200 300 5000
  run _format_usage "$TMPDIR/small.jsonl"
  [[ "$output" == *'500 tokens'* ]]
}

@test "_format_usage: token formatting — over 1k shows k suffix" {
  _make_session_log "$TMPDIR/medium.jsonl" 1.00 800 700 10000
  run _format_usage "$TMPDIR/medium.jsonl"
  [[ "$output" == *'2k tokens'* ]]
}

@test "_format_usage: token formatting — over 1M shows M suffix" {
  _make_session_log "$TMPDIR/large.jsonl" 10.00 500000 600000 300000 100000 50000
  run _format_usage "$TMPDIR/large.jsonl"
  [[ "$output" == *'M tokens'* ]]
}

@test "_format_usage: duration formatting — seconds only" {
  _make_session_log "$TMPDIR/short.jsonl" 0.50 100 100 45000
  run _format_usage "$TMPDIR/short.jsonl"
  [[ "$output" == *'45s'* ]]
}

@test "_format_usage: duration formatting — minutes and seconds" {
  _make_session_log "$TMPDIR/long.jsonl" 5.00 1000 1000 125000
  run _format_usage "$TMPDIR/long.jsonl"
  [[ "$output" == *'2m 5s'* ]]
}

@test "_format_usage: cost formatting rounds to 2 decimals" {
  _make_session_log "$TMPDIR/cost.jsonl" 3.456 100 100 1000
  run _format_usage "$TMPDIR/cost.jsonl"
  [[ "$output" == *'$3.46'* ]]
}

@test "_format_usage: includes cache tokens in total" {
  _make_session_log "$TMPDIR/cache.jsonl" 1.00 100 200 10000 5000 3000
  run _format_usage "$TMPDIR/cache.jsonl"
  # 100 + 200 + 5000 + 3000 = 8300 -> 8k
  [[ "$output" == *'8k tokens'* ]]
}

# ── _archive_review ──────────────────────────────────────────────────────────

@test "_archive_review: existing review creates prior and timestamped archive" {
  local review_file="$TMPDIR/reviews/test-repo-42.md"
  local session_log="$TMPDIR/reviews/test-repo-42.session.jsonl"
  mkdir -p "$TMPDIR/reviews"
  echo "old review" > "$review_file"
  echo "old session" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -f "$prior_path" ]
  [[ "$prior_path" == *".prior.md" ]]
  [ "$(cat "$prior_path")" = "old review" ]
  # Original files should be moved (not exist at original path)
  [ ! -f "$review_file" ]
  [ ! -f "$session_log" ]
  # Timestamped archives should exist
  local md_archives
  md_archives=$(ls "$TMPDIR/reviews/test-repo-42".2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_archives" -eq 1 ]
}

@test "_archive_review: no existing review sets empty prior_path" {
  local review_file="$TMPDIR/reviews/test-repo-99.md"
  local session_log="$TMPDIR/reviews/test-repo-99.session.jsonl"
  mkdir -p "$TMPDIR/reviews"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -z "$prior_path" ]
}

@test "_archive_review: prunes old archives beyond ARCHIVE_KEEP_COUNT" {
  local review_file="$TMPDIR/reviews/test-repo-1.md"
  local session_log="$TMPDIR/reviews/test-repo-1.session.jsonl"
  mkdir -p "$TMPDIR/reviews"

  # Create 5 existing timestamped archives (older than any new archive)
  for i in 1 2 3 4 5; do
    echo "archive $i" > "$TMPDIR/reviews/test-repo-1.2025010${i}-120000.md"
    echo "session $i" > "$TMPDIR/reviews/test-repo-1.session.2025010${i}-120000.jsonl"
  done

  # Create the current review to be archived
  echo "current review" > "$review_file"
  echo "current session" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  # Should keep only ARCHIVE_KEEP_COUNT (3) md archives
  local md_count
  md_count=$(ls "$TMPDIR/reviews/test-repo-1".2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_count" -le "$ARCHIVE_KEEP_COUNT" ]
}

# ── _extract_repo ────────────────────────────────────────────────────────────

@test "_extract_repo: GitHub URL extracts owner/repo" {
  local result
  _extract_repo result "https://github.com/org/my-repo/pull/42"
  [ "$result" = "org/my-repo" ]
}

@test "_extract_repo: GitHub URL with .git suffix" {
  local result
  _extract_repo result "https://github.com/org/my-repo"
  [ "$result" = "org/my-repo" ]
}
