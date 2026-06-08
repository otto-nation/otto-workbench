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

# ── _is_pr_ref ───────────────────────────────────────────────────────────────

@test "_is_pr_ref: bare PR number returns true" {
  _is_pr_ref "42"
}

@test "_is_pr_ref: GitHub URL returns true" {
  _is_pr_ref "https://github.com/org/repo/pull/123"
}

@test "_is_pr_ref: branch name returns false" {
  ! _is_pr_ref "isaac/feat/dream_scripts"
}

@test "_is_pr_ref: branch with numbers returns false" {
  ! _is_pr_ref "isaac/fix/PR-123-review"
}

@test "_is_pr_ref: empty string returns false" {
  ! _is_pr_ref ""
}

# ── _review_file ─────────────────────────────────────────────────────────────

@test "_review_file: constructs folder-based path from org/repo and PR number" {
  local result
  _review_file result "org/my-repo" "42"
  [ "$result" = "$REVIEWS_DIR/my-repo-42/review.md" ]
}

@test "_review_file: repo name with hyphens preserved" {
  local result
  _review_file result "org/my-cool-repo" "1"
  [ "$result" = "$REVIEWS_DIR/my-cool-repo-1/review.md" ]
}

@test "_review_file: strips only last path component" {
  local result
  _review_file result "deep/nested/repo" "7"
  [ "$result" = "$REVIEWS_DIR/repo-7/review.md" ]
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
  mkdir -p "$TMPDIR/reviews/test-repo-42"
  local review_file="$TMPDIR/reviews/test-repo-42/review.md"
  local session_log="$TMPDIR/reviews/test-repo-42/session.jsonl"
  echo "old review" > "$review_file"
  echo "old session" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -f "$prior_path" ]
  [[ "$prior_path" == *"/prior.md" ]]
  [ "$(cat "$prior_path")" = "old review" ]
  # Original files should be moved (not exist at original path)
  [ ! -f "$review_file" ]
  [ ! -f "$session_log" ]
  # Timestamped archives should exist in archives/ subdirectory
  local md_archives
  md_archives=$(ls "$TMPDIR/reviews/test-repo-42/archives/"2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_archives" -eq 1 ]
}

@test "_archive_review: no existing review sets empty prior_path" {
  mkdir -p "$TMPDIR/reviews/test-repo-99"
  local review_file="$TMPDIR/reviews/test-repo-99/review.md"
  local session_log="$TMPDIR/reviews/test-repo-99/session.jsonl"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -z "$prior_path" ]
}

@test "_archive_review: prunes old archives beyond ARCHIVE_KEEP_COUNT" {
  mkdir -p "$TMPDIR/reviews/test-repo-1/archives"
  local review_file="$TMPDIR/reviews/test-repo-1/review.md"
  local session_log="$TMPDIR/reviews/test-repo-1/session.jsonl"

  # Create 5 existing timestamped archives (older than any new archive)
  for i in 1 2 3 4 5; do
    echo "archive $i" > "$TMPDIR/reviews/test-repo-1/archives/2025010${i}-120000.md"
    echo "session $i" > "$TMPDIR/reviews/test-repo-1/archives/2025010${i}-120000.session.jsonl"
  done

  # Create the current review to be archived
  echo "current review" > "$review_file"
  echo "current session" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  # Should keep only ARCHIVE_KEEP_COUNT (3) md archives
  local md_count
  md_count=$(ls "$TMPDIR/reviews/test-repo-1/archives/"2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_count" -le "$ARCHIVE_KEEP_COUNT" ]
}

@test "_archive_review: intermediates inside folder are untouched" {
  mkdir -p "$TMPDIR/reviews/test-repo-50"
  local review_file="$TMPDIR/reviews/test-repo-50/review.md"
  local session_log="$TMPDIR/reviews/test-repo-50/session.jsonl"

  # Create intermediates inside the review folder (from a prior multi-phase run)
  echo "group1" > "$TMPDIR/reviews/test-repo-50/group-1.jsonl"
  echo "group1md" > "$TMPDIR/reviews/test-repo-50/group-1.md"
  echo "meta" > "$TMPDIR/reviews/test-repo-50/meta.json"

  # No current review file — archive should be a no-op
  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -z "$prior_path" ]
  # Intermediates should still be present (archive only touches review/session/post)
  [ -f "$TMPDIR/reviews/test-repo-50/group-1.jsonl" ]
  [ -f "$TMPDIR/reviews/test-repo-50/group-1.md" ]
  [ -f "$TMPDIR/reviews/test-repo-50/meta.json" ]
}

@test "_archive_review: archives post.jsonl with timestamp" {
  mkdir -p "$TMPDIR/reviews/test-repo-60"
  local review_file="$TMPDIR/reviews/test-repo-60/review.md"
  local session_log="$TMPDIR/reviews/test-repo-60/session.jsonl"
  echo "review" > "$review_file"
  echo "post data" > "$TMPDIR/reviews/test-repo-60/post.jsonl"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  # post.jsonl should be archived (moved to timestamped version in archives/)
  [ ! -f "$TMPDIR/reviews/test-repo-60/post.jsonl" ]
  local post_archives
  post_archives=$(ls "$TMPDIR/reviews/test-repo-60/archives/"2*.post.jsonl 2>/dev/null | wc -l | tr -d ' ')
  [ "$post_archives" -eq 1 ]
}

# ── _prune_merged_reviews ────────────────────────────────────────────────────

@test "_prune_merged_reviews: removes folder for merged PR" {
  local reviews_dir="$TMPDIR/reviews"
  mkdir -p "$reviews_dir/my-repo-42"

  echo "review content" > "$reviews_dir/my-repo-42/review.md"
  echo "session data" > "$reviews_dir/my-repo-42/session.jsonl"
  echo '{"repo":"org/my-repo","pr_number":"42","head_sha":"abc"}' > "$reviews_dir/my-repo-42/meta.json"

  local fake_bin="$TMPDIR/bin"
  mkdir -p "$fake_bin"
  cat > "$fake_bin/gh" <<'GHEOF'
#!/usr/bin/env bash
echo "MERGED"
GHEOF
  chmod +x "$fake_bin/gh"

  REVIEWS_DIR="$reviews_dir" PATH="$fake_bin:$PATH" run bash -c \
    'export HOME="$1"; NO_COLOR=1 source "$2" && REVIEWS_DIR="$3" _prune_merged_reviews' \
    -- "$TMPDIR" "$CLAUDE_REVIEW" "$reviews_dir"
  [ "$status" -eq 0 ]

  [ ! -d "$reviews_dir/my-repo-42" ]
}

@test "_prune_merged_reviews: keeps folder for open PR" {
  local reviews_dir="$TMPDIR/reviews"
  mkdir -p "$reviews_dir/my-repo-99"

  echo "review content" > "$reviews_dir/my-repo-99/review.md"
  echo '{"repo":"org/my-repo","pr_number":"99","head_sha":"def"}' > "$reviews_dir/my-repo-99/meta.json"

  local fake_bin="$TMPDIR/bin"
  mkdir -p "$fake_bin"
  cat > "$fake_bin/gh" <<'GHEOF'
#!/usr/bin/env bash
echo "OPEN"
GHEOF
  chmod +x "$fake_bin/gh"

  REVIEWS_DIR="$reviews_dir" PATH="$fake_bin:$PATH" run bash -c \
    'export HOME="$1"; NO_COLOR=1 source "$2" && REVIEWS_DIR="$3" _prune_merged_reviews' \
    -- "$TMPDIR" "$CLAUDE_REVIEW" "$reviews_dir"
  [ "$status" -eq 0 ]

  [ -d "$reviews_dir/my-repo-99" ]
  [ -f "$reviews_dir/my-repo-99/review.md" ]
  [ -f "$reviews_dir/my-repo-99/meta.json" ]
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

# ── _archive_review: self-review paths ──────────────────────────────────────

@test "_archive_review: works with self-review paths in ignore/reviews/" {
  mkdir -p "$TMPDIR/project/ignore/reviews"
  local review_file="$TMPDIR/project/ignore/reviews/self-review.md"
  local session_log="$TMPDIR/project/ignore/reviews/session.jsonl"
  echo "self-review content" > "$review_file"
  echo "session data" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  [ -f "$prior_path" ]
  [[ "$prior_path" == *"ignore/reviews/prior.md" ]]
  [ "$(cat "$prior_path")" = "self-review content" ]
  [ ! -f "$review_file" ]
  [ ! -f "$session_log" ]
  local md_archives
  md_archives=$(ls "$TMPDIR/project/ignore/reviews/archives/"2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_archives" -eq 1 ]
  local session_archives
  session_archives=$(ls "$TMPDIR/project/ignore/reviews/archives/"2*.session.jsonl 2>/dev/null | wc -l | tr -d ' ')
  [ "$session_archives" -eq 1 ]
}

@test "_archive_review: self-review prunes old archives" {
  mkdir -p "$TMPDIR/project/ignore/reviews/archives"
  local review_file="$TMPDIR/project/ignore/reviews/self-review.md"
  local session_log="$TMPDIR/project/ignore/reviews/session.jsonl"

  for i in 1 2 3 4 5; do
    echo "archive $i" > "$TMPDIR/project/ignore/reviews/archives/2025010${i}-120000.md"
    echo "session $i" > "$TMPDIR/project/ignore/reviews/archives/2025010${i}-120000.session.jsonl"
  done

  echo "current" > "$review_file"
  echo "current session" > "$session_log"

  local prior_path
  _archive_review prior_path "$review_file" "$session_log"

  local md_count
  md_count=$(ls "$TMPDIR/project/ignore/reviews/archives/"2*.md 2>/dev/null | wc -l | tr -d ' ')
  [ "$md_count" -le "$ARCHIVE_KEEP_COUNT" ]
}

# ── _append_orchestrate_flags ───────────────────────────────────────────────

@test "_append_orchestrate_flags: no optional flags returns 0 under set -e" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    set -e
    declare -a args=()
    _append_orchestrate_flags args "false" "" ""
    echo "${args[@]}"
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--generator-version"* ]]
  [[ "$output" != *"--no-holistic"* ]]
  [[ "$output" != *"--max-cost"* ]]
  [[ "$output" != *"--model"* ]]
}

@test "_append_orchestrate_flags: all flags present when set" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    set -e
    declare -a args=()
    _append_orchestrate_flags args "true" "5" "sonnet"
    echo "${args[@]}"
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--generator-version"* ]]
  [[ "$output" == *"--no-holistic"* ]]
  [[ "$output" == *"--max-cost 5"* ]]
  [[ "$output" == *"--model sonnet"* ]]
}

@test "_append_orchestrate_flags: mixed flags — only max-cost set" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    set -e
    declare -a args=()
    _append_orchestrate_flags args "false" "20" ""
    echo "${args[@]}"
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--max-cost 20"* ]]
  [[ "$output" != *"--no-holistic"* ]]
  [[ "$output" != *"--model"* ]]
}

# ── ERR trap ───────────────────────────────────────────────────────────────

@test "ERR trap fires with diagnostic output on unexpected failure" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    _will_fail() { false; }
    _will_fail
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Unexpected failure"* ]]
  [[ "$output" == *"_will_fail"* ]]
}

# ── self-review rule ────────────────────────────────────────────────────────

@test "self-review rule does not suggest --no-post" {
  local rule_file="$REPO_ROOT/ai/guidelines/rules/self-review.md"
  [ -f "$rule_file" ]
  run grep -c '\-\-no-post' "$rule_file"
  [ "$output" = "0" ]
}

# ── cmd_self_review parameter ordering ──────────────────────────────────────

@test "cmd_self_review: force parameter is accepted at position 5" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    # Verify the function signature accepts force at position 5
    declare -f cmd_self_review | grep -q "force=.*5"
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
}

@test "main: --force is forwarded to cmd_self_review at position 5" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    cmd_self_review() {
      local IFS="|"; echo "$*"
    }
    main --self --force 2>/dev/null
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
  # Args: pr_input|issue_link|max_parallel|skip_user_verification|force|no_holistic|max_cost|model|resume
  IFS='|' read -ra args <<< "$output"
  [ "${args[4]}" = "true" ]
}

# ── Auto-resume (pipeline state detection) ──────────────────────────────────

@test "usage: --resume removed from help text" {
  run usage
  [[ "$output" != *"--resume"* ]]
}

@test "cmd_review: pipeline state skips _archive_review" {
  archive_called="false"
  _archive_review() { archive_called="true"; local -n __out=$1; __out=""; }
  _extract_pr_number() { local -n __out=$1; __out="42"; }
  _extract_repo() { local -n __out=$1; __out="org/repo"; }
  mkdir -p "$TMPDIR/reviews/repo-42"
  _review_file() { local -n __out=$1; __out="$TMPDIR/reviews/repo-42/review.md"; }
  _check_stale_review() { :; }
  _check_pending_review() { :; }
  _setup_review_worktree() { local -n __out=$1; __out="/tmp/wt"; }
  _append_orchestrate_flags() { :; }
  _run_orchestrate() { touch "$TMPDIR/reviews/repo-42/review.md"; return 0; }
  _prune_merged_reviews() { :; }
  _display_review() { :; }
  _print_summary() { :; }
  gh() { echo "{}"; }

  # Create pipeline state to trigger auto-resume
  echo '{}' > "$TMPDIR/reviews/repo-42/pipeline.json"
  cmd_review "42" "true" "" "false" "4" "false" "false" "" "" 2>/dev/null || true
  [ "$archive_called" = "false" ]
  rm -f "$TMPDIR/reviews/repo-42/pipeline.json"
}

@test "cmd_review: no --resume in orchestrate_args" {
  local fn_body
  fn_body=$(declare -f cmd_review)
  [[ "$fn_body" != *'orchestrate_args+=(--resume)'* ]]
}

@test "_resolve_prior_review: resume with no prior file returns 0 under set -e" {
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    _test_resolve() {
      set -e
      local prior=""
      _resolve_prior_review prior "$3/nonexistent.md" "$3/nonexistent.session.jsonl" "true"
      echo "prior=$prior"
    }
    _test_resolve
  ' -- "$TMPDIR" "$CLAUDE_REVIEW" "$TMPDIR"
  [ "$status" -eq 0 ]
  [ "$output" = "prior=" ]
}

@test "_append_orchestrate_flags: empty model does not exit under set -e" {
  local args=()
  _append_orchestrate_flags args "false" "" ""
  [[ ${#args[@]} -ge 1 ]]
}

@test "auto-resume: pipeline state detection in cmd_review" {
  local fn_body
  fn_body=$(declare -f cmd_review)
  [[ "$fn_body" == *'FILENAME_PIPELINE_STATE'* ]]
  [[ "$fn_body" == *'has_pipeline_state'* ]]
}

@test "auto-resume: pipeline state detection in cmd_self_review" {
  local fn_body
  fn_body=$(declare -f cmd_self_review)
  [[ "$fn_body" == *'FILENAME_PIPELINE_STATE'* ]]
  [[ "$fn_body" == *'has_pipeline_state'* ]]
}

# ── _count_severity ────────────────────────────────────────────────────────

@test "_count_severity: counts must-fix findings" {
  cat > "$TMPDIR/review.md" <<'EOF'
## Must fix
- **[M1]** path:1 — description
- **[M2]** path:2 — description
## Should fix
- **[S1]** path:3 — description
EOF
  local count
  _count_severity count "$TMPDIR/review.md" "M"
  [ "$count" -eq 2 ]
}

@test "_count_severity: excludes struck-through findings" {
  cat > "$TMPDIR/review.md" <<'EOF'
## Must fix
- **[M1]** path:1 — active
- ~~**[M2]** path:2 — resolved~~
EOF
  local count
  _count_severity count "$TMPDIR/review.md" "M"
  [ "$count" -eq 1 ]
}

@test "_count_severity: counts checkbox findings" {
  cat > "$TMPDIR/review.md" <<'EOF'
## Must fix
- [ ] **[M1]** path:1 — with checkbox
- **[M2]** path:2 — without checkbox
EOF
  local count
  _count_severity count "$TMPDIR/review.md" "M"
  [ "$count" -eq 2 ]
}

@test "_count_severity: returns 0 for missing file" {
  local count
  _count_severity count "$TMPDIR/nonexistent.md" "M"
  [ "$count" -eq 0 ]
}

@test "_count_severity: returns 0 for empty file" {
  touch "$TMPDIR/empty.md"
  local count
  _count_severity count "$TMPDIR/empty.md" "M"
  [ "$count" -eq 0 ]
}

# ── _json_summary ──────────────────────────────────────────────────────────

@test "_json_summary: complete review with findings" {
  cat > "$TMPDIR/review.md" <<'EOF'
## Must fix
- **[M1]** path:1 — bug
## Should fix
- **[S1]** path:2 — improvement
- **[S2]** path:3 — improvement
## Nit
- **[N1]** path:4 — style
## Idioms
- **[I1]** path:5 — idiom
- **[I2]** path:6 — idiom
EOF
  run _json_summary "org/repo" "42" "$TMPDIR/review.md"
  [ "$status" -eq 0 ]
  local json="${output#REVIEW_SUMMARY:}"
  [ "$(echo "$json" | jq -r '.repo')" = "org/repo" ]
  [ "$(echo "$json" | jq '.pr_number')" = "42" ]
  [ "$(echo "$json" | jq '.findings.must_fix')" = "1" ]
  [ "$(echo "$json" | jq '.findings.should_fix')" = "2" ]
  [ "$(echo "$json" | jq '.findings.nit')" = "1" ]
  [ "$(echo "$json" | jq '.findings.idiom')" = "2" ]
  [ "$(echo "$json" | jq '.findings.total')" = "6" ]
  [ "$(echo "$json" | jq -r '.verdict')" = "changes_requested" ]
  [ "$(echo "$json" | jq '.cost_usd')" = "0" ]
  [ "$(echo "$json" | jq '.duration_ms')" = "0" ]
}

@test "_json_summary: approve when no must-fix" {
  cat > "$TMPDIR/review.md" <<'EOF'
## Should fix
- **[S1]** path:1 — improvement
## Nit
- **[N1]** path:2 — style
EOF
  run _json_summary "org/repo" "10" "$TMPDIR/review.md"
  [ "$status" -eq 0 ]
  local json="${output#REVIEW_SUMMARY:}"
  [ "$(echo "$json" | jq -r '.verdict')" = "approve" ]
  [ "$(echo "$json" | jq '.findings.total')" = "2" ]
}

@test "_json_summary: missing review file" {
  run _json_summary "org/repo" "42" "$TMPDIR/nonexistent.md"
  [ "$status" -eq 0 ]
  local json="${output#REVIEW_SUMMARY:}"
  [ "$(echo "$json" | jq '.findings.total')" = "0" ]
  [ "$(echo "$json" | jq -r '.verdict')" = "approve" ]
}

@test "_json_summary: self-review without PR" {
  cat > "$TMPDIR/self-review.md" <<'EOF'
## Must fix
- **[M1]** path:1 — bug
EOF
  run _json_summary "org/repo" "" "$TMPDIR/self-review.md"
  [ "$status" -eq 0 ]
  local json="${output#REVIEW_SUMMARY:}"
  [ "$(echo "$json" | jq '.pr_number')" = "null" ]
  [ "$(echo "$json" | jq -r '.verdict')" = "changes_requested" ]
}

# ── --json-summary flag ────────────────────────────────────────────────────

@test "usage: --json-summary appears in help text" {
  run usage
  [[ "$output" == *"--json-summary"* ]]
}

# ── _has_recoverable_failures ──────────────────────────────────────────────

@test "_has_recoverable_failures: completed pipeline with group failures returns true" {
  local pipeline_state="$TMPDIR/test.pipeline.json"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":true,"groups_done":[],"groups_failed":{"1":"error"},"synthesis_done":true,"synthesis_failed":""}' > "$pipeline_state"
  _has_recoverable_failures "$pipeline_state"
}

@test "_has_recoverable_failures: completed pipeline with synthesis failure returns true" {
  local pipeline_state="$TMPDIR/test.pipeline.json"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":true,"groups_done":[1],"groups_failed":{},"synthesis_done":true,"synthesis_failed":"timeout"}' > "$pipeline_state"
  _has_recoverable_failures "$pipeline_state"
}

@test "_has_recoverable_failures: completed pipeline without failures returns false" {
  local pipeline_state="$TMPDIR/test.pipeline.json"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":true,"groups_done":[1],"groups_failed":{},"synthesis_done":true,"synthesis_failed":""}' > "$pipeline_state"
  ! _has_recoverable_failures "$pipeline_state"
}

@test "_has_recoverable_failures: incomplete pipeline returns false" {
  local pipeline_state="$TMPDIR/test.pipeline.json"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":false,"groups_done":[],"groups_failed":{},"synthesis_done":false,"synthesis_failed":""}' > "$pipeline_state"
  ! _has_recoverable_failures "$pipeline_state"
}

@test "_has_recoverable_failures: missing file returns false" {
  ! _has_recoverable_failures "$TMPDIR/nonexistent.pipeline.json"
}

# ── _resolve_prior_review: recovery-aware ──────────────────────────────────

@test "_resolve_prior_review: completed pipeline with failures skips archive" {
  mkdir -p "$TMPDIR/reviews/test-repo-42"
  local review_file="$TMPDIR/reviews/test-repo-42/review${REVIEW_EXT}"
  local session_log="$TMPDIR/reviews/test-repo-42/${FILENAME_SESSION}"
  local pipeline_state="$TMPDIR/reviews/test-repo-42/${FILENAME_PIPELINE_STATE}"
  echo '## Review' > "$review_file"
  echo '{}' > "$session_log"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":true,"groups_done":[],"groups_failed":{"1":"error"},"synthesis_done":true,"synthesis_failed":""}' > "$pipeline_state"

  local prior
  _resolve_prior_review prior "$review_file" "$session_log" "false"

  # Should NOT archive — recovery mode
  [ -f "$review_file" ]
  # Prior should be set for the orchestrator
  [ -n "$prior" ]
}

@test "_resolve_prior_review: completed pipeline without failures archives normally" {
  mkdir -p "$TMPDIR/reviews/test-repo-43"
  local review_file="$TMPDIR/reviews/test-repo-43/review${REVIEW_EXT}"
  local session_log="$TMPDIR/reviews/test-repo-43/${FILENAME_SESSION}"
  local pipeline_state="$TMPDIR/reviews/test-repo-43/${FILENAME_PIPELINE_STATE}"
  echo '## Review' > "$review_file"
  echo '{}' > "$session_log"
  echo '{"head_sha":"abc","group_names":["g1"],"holistic_done":true,"groups_done":[1],"groups_failed":{},"synthesis_done":true,"synthesis_failed":""}' > "$pipeline_state"

  local prior
  _resolve_prior_review prior "$review_file" "$session_log" "false"

  # Successful completed review — should archive
  [ ! -f "$review_file" ]
  # Pipeline state should be cleaned up
  [ ! -f "$pipeline_state" ]
}

# ── --json-summary flag ────────────────────────────────────────────────────

@test "cmd_gc: removes review folders with no review file older than 7 days" {
  mkdir -p "$TMPDIR/reviews/test-repo-100"
  echo '{}' > "$TMPDIR/reviews/test-repo-100/pipeline.json"
  touch -t 202605010000 "$TMPDIR/reviews/test-repo-100/pipeline.json"
  echo '{}' > "$TMPDIR/reviews/test-repo-100/group-1.jsonl"
  touch -t 202605010000 "$TMPDIR/reviews/test-repo-100/group-1.jsonl"

  # Has a review file — should NOT be cleaned
  mkdir -p "$TMPDIR/reviews/test-repo-200"
  echo '## Review' > "$TMPDIR/reviews/test-repo-200/review.md"
  echo '{}' > "$TMPDIR/reviews/test-repo-200/pipeline.json"
  touch -t 202605010000 "$TMPDIR/reviews/test-repo-200/pipeline.json"

  run bash -c '
    export HOME="'"$TMPDIR"'"
    NO_COLOR=1 source "'"$CLAUDE_REVIEW"'"
    REVIEWS_DIR="'"$TMPDIR/reviews"'" cmd_gc
  '
  [ "$status" -eq 0 ]
  [ ! -d "$TMPDIR/reviews/test-repo-100" ]
  [ -d "$TMPDIR/reviews/test-repo-200" ]
}

@test "cmd_gc: removes stale intermediates from completed reviews" {
  mkdir -p "$TMPDIR/reviews/test-repo-300"
  echo '## Summary' > "$TMPDIR/reviews/test-repo-300/review.md"
  echo '{}' > "$TMPDIR/reviews/test-repo-300/group-1.md"
  echo '{}' > "$TMPDIR/reviews/test-repo-300/group-1.jsonl"
  echo '{}' > "$TMPDIR/reviews/test-repo-300/holistic.md"
  echo '{}' > "$TMPDIR/reviews/test-repo-300/holistic.jsonl"
  # No pipeline.json — review is complete
  touch -t 202605010000 "$TMPDIR/reviews/test-repo-300/group-1.md"
  touch -t 202605010000 "$TMPDIR/reviews/test-repo-300/group-1.jsonl"

  run bash -c '
    export HOME="'"$TMPDIR"'"
    NO_COLOR=1 source "'"$CLAUDE_REVIEW"'"
    REVIEWS_DIR="'"$TMPDIR/reviews"'" cmd_gc
  '
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/reviews/test-repo-300/review.md" ]
  [ ! -f "$TMPDIR/reviews/test-repo-300/group-1.md" ]
  [ ! -f "$TMPDIR/reviews/test-repo-300/group-1.jsonl" ]
  [ ! -f "$TMPDIR/reviews/test-repo-300/holistic.md" ]
  [ ! -f "$TMPDIR/reviews/test-repo-300/holistic.jsonl" ]
}

@test "cmd_gc: preserves intermediates when pipeline is active" {
  mkdir -p "$TMPDIR/reviews/test-repo-400"
  echo '{}' > "$TMPDIR/reviews/test-repo-400/pipeline.json"
  echo '{}' > "$TMPDIR/reviews/test-repo-400/group-1.jsonl"
  # Recent timestamps — active pipeline
  touch "$TMPDIR/reviews/test-repo-400/pipeline.json"
  touch "$TMPDIR/reviews/test-repo-400/group-1.jsonl"

  run bash -c '
    export HOME="'"$TMPDIR"'"
    NO_COLOR=1 source "'"$CLAUDE_REVIEW"'"
    REVIEWS_DIR="'"$TMPDIR/reviews"'" cmd_gc
  '
  [ "$status" -eq 0 ]
  [ -d "$TMPDIR/reviews/test-repo-400" ]
  [ -f "$TMPDIR/reviews/test-repo-400/group-1.jsonl" ]
}

@test "main: --json-summary is parsed and not treated as positional" {
  # Mock all external dependencies so nothing touches gh or the filesystem.
  # If --json-summary were treated as positional, _extract_pr_number would fail
  # with "Cannot extract PR number from: --json-summary".
  run bash -c '
    export HOME="$1"; NO_COLOR=1
    source "$2"
    cmd_review() { :; }
    _extract_repo() { local -n __out=$1; __out="org/repo"; }
    _review_file() { local -n __out=$1; __out="/tmp/review.md"; }
    _json_summary() { echo "{}"; }
    main --json-summary 42
  ' -- "$TMPDIR" "$CLAUDE_REVIEW"
  [ "$status" -eq 0 ]
}
