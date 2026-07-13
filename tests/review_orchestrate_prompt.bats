#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # warm .pyc cache; errors caught at import
  python3 -m compileall -q "$REPO_ROOT/ai/claude/lib" "$REPO_ROOT/ai/claude/bin" 2>/dev/null || true
  export ORCHESTRATE="$REPO_ROOT/ai/claude/bin/review-orchestrate"
}

setup() {
  load 'test_helper'
  load 'review_orchestrate_helper'
  common_setup
  TMPDIR="$BATS_TEST_TMPDIR"
}

teardown() {
  common_teardown
}

@test "render_template: substitutes variables" {
  result=$(_py '
result = mod.render_template("single-agent.md",
    pr_number="42", repo="test/repo",
    pr_header="## PR metadata", reviews_section="## Reviews",
    env_section="## Env", issue_section="", prior_section="",
    review_file="/tmp/review.md",
)
print(result)
')
  [[ "$result" == *"PR #42"* ]]
  [[ "$result" == *"test/repo"* ]]
}

@test "render_template: safe_substitute leaves unknown vars" {
  result=$(_py '
result = mod.render_template("single-agent.md",
    pr_number="1", repo="r",
    pr_header="h", reviews_section="r",
    env_section="e", review_file="f",
)
print("ok" if "${" not in result or "issue_section" in result else "fail")
')
  [ "$result" = "ok" ]
}


# ── Prompt building ──────────────────────────────────────────────────────────

# ── Model selection ───────────────────────────────────────────────────────────

@test "model defaults: group uses sonnet, others use opus" {
  result=$(_py "
print(mod.DEFAULT_MODEL_GROUP)
print(mod.DEFAULT_MODEL_HOLISTIC)
print(mod.DEFAULT_MODEL_SYNTHESIS)
print(mod.DEFAULT_MODEL_SINGLE)
")
  lines=()
  while IFS= read -r line; do lines+=("$line"); done <<< "$result"
  [ "${lines[0]}" = "sonnet" ]
  [ "${lines[1]}" = "opus" ]
  [ "${lines[2]}" = "opus" ]
  [ "${lines[3]}" = "opus" ]
}

@test "_resolve_model: explicit override wins" {
  result=$(_py "
import os
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model('haiku', 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: env var beats default" {
  result=$(_py "
import os
os.environ['CLAUDE_REVIEW_GROUP_MODEL'] = 'haiku'
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
del os.environ['CLAUDE_REVIEW_GROUP_MODEL']
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: global env var beats default" {
  result=$(_py "
import os
os.environ['CLAUDE_REVIEW_MODEL'] = 'haiku'
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
del os.environ['CLAUDE_REVIEW_MODEL']
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: default when no override" {
  result=$(_py "
import os
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
")
  [ "$result" = "sonnet" ]
}

# ── Prompt building ──────────────────────────────────────────────────────────

@test "build_prompt: single-agent includes review file" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="desc", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=2,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix it", reviews="[]", review_comments="[]", comments="[]")
job = mod.ReviewJob(
    repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("single-agent.md", job, max_turns=15)
print(result)
')
  [[ "$result" == *"PR #99"* ]]
  [[ "$result" == *"/tmp/review.md"* ]]
}

@test "build_prompt: group includes holistic block when content set" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("group.md", job, max_turns=15,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    group_output="/tmp/g1.md",
    holistic_content="Watch for API mismatches",
)
print("FOUND" if "Holistic context" in result else "MISSING")
')
  [ "$result" = "FOUND" ]
}

@test "build_prompt: group omits holistic block when empty" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("group.md", job, max_turns=15,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    group_output="/tmp/g1.md",
    holistic_content="",
)
print("FOUND" if "Holistic context" in result else "MISSING")
')
  [ "$result" = "MISSING" ]
}

