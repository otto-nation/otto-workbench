#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # warm .pyc cache; errors caught at import
  python3 -m compileall -q "$REPO_ROOT/ai/lib" "$REPO_ROOT/ai/bin" 2>/dev/null || true
  export ORCHESTRATE="$REPO_ROOT/ai/bin/review-orchestrate"
}

setup() {
  load 'test_helper'
  load 'review_orchestrate_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "agent_templates.render: substitutes variables" {
  result=$(_py '
import agent.templates
result = agent.templates.render("single-agent.md",
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

@test "agent_templates.render: safe_substitute leaves unknown vars" {
  result=$(_py '
import agent.templates
result = agent.templates.render("single-agent.md",
    pr_number="1", repo="r",
    pr_header="h", reviews_section="r",
    env_section="e", review_file="f",
)
print("ok" if "${" not in result or "issue_section" in result else "fail")
')
  [ "$result" = "ok" ]
}

# ── Model selection ───────────────────────────────────────────────────────────

@test "model defaults: every phase names a tier and no model" {
  result=$(_py "
specs = mod.PHASES.values()
print(all(s.tier is not None for s in specs), any(hasattr(s, 'model') for s in specs))
")
  [ "$result" = "True False" ]
}

@test "model defaults: registry covers every phase" {
  result=$(_py "
print(sorted(mod.PHASES) == sorted(mod.Phase))
")
  [ "$result" = "True" ]
}

# The env layers, through the public resolver. The tier beneath them is pinned
# in tests/agent_models_test.py; the config file is sandboxed here and names
# no tier, so a phase with no env layer resolves to nothing.

@test "phase_model: explicit override wins over the env" {
  result=$(_py "
import os
os.environ.pop('WORKBENCH_AI_MODEL', None)
os.environ['WORKBENCH_AI_GROUP_MODEL'] = 'claude-sonnet-5'
print(mod.phase_model('group', 'claude-haiku-4-5'))
")
  [ "$result" = "claude-haiku-4-5" ]
}

@test "phase_model: the phase env var answers" {
  result=$(_py "
import os
os.environ['WORKBENCH_AI_GROUP_MODEL'] = 'claude-haiku-4-5'
os.environ.pop('WORKBENCH_AI_MODEL', None)
print(mod.phase_model('group', None))
")
  [ "$result" = "claude-haiku-4-5" ]
}

@test "phase_model: the global env var answers" {
  result=$(_py "
import os
os.environ['WORKBENCH_AI_MODEL'] = 'claude-haiku-4-5'
os.environ.pop('WORKBENCH_AI_GROUP_MODEL', None)
print(mod.phase_model('group', None))
")
  [ "$result" = "claude-haiku-4-5" ]
}

@test "phase_model: None when no layer names a model" {
  result=$(_py "
import os
os.environ.pop('WORKBENCH_AI_MODEL', None)
os.environ.pop('WORKBENCH_AI_GROUP_MODEL', None)
print(mod.phase_model('group', None))
")
  [ "$result" = "None" ]
}

@test "phase_model: tier env vars do not rewrite a Claude tier word" {
  result=$(_py "
import os
os.environ.pop('WORKBENCH_AI_MODEL', None)
os.environ.pop('WORKBENCH_AI_GROUP_MODEL', None)
os.environ['AI_SONNET_MODEL'] = 'claude-sonnet-5'
os.environ['ANTHROPIC_DEFAULT_SONNET_MODEL'] = 'claude-sonnet-4-5'
print(mod.phase_model('group', 'sonnet'), mod.phase_model('group', None))
")
  [ "$result" = "sonnet None" ]
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
    session_log="/tmp/session.jsonl",
)
result = mod.build_prompt(mod.Phase.SINGLE, job, max_turns=15)
print(result)
')
  [[ "$result" == *"PR #99"* ]]
  [[ "$result" == *"/tmp/single.json"* ]]
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
    session_log="/tmp/session.jsonl",
)
result = mod.build_prompt(mod.Phase.GROUP, job, max_turns=15,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    holistic_content="## Holistic context\nWatch for API mismatches",
)
print("FOUND" if "Watch for API mismatches" in result else "MISSING")
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
    session_log="/tmp/session.jsonl",
)
result = mod.build_prompt(mod.Phase.GROUP, job, max_turns=15,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    holistic_content="",
)
print("FOUND" if "Holistic context" in result else "MISSING")
')
  [ "$result" = "MISSING" ]
}

@test "group.md turn-budget line allows complete rewrites" {
  grep -q "Further complete rewrites are expected" "$REPO_ROOT/ai/lib/review-templates/group.md"
  ! grep -q "do not build the file up in pieces" "$REPO_ROOT/ai/lib/review-templates/group.md"
}

@test "single-agent.md turn-budget line allows complete rewrites" {
  grep -q "Further complete rewrites are expected" "$REPO_ROOT/ai/lib/review-templates/single-agent.md"
  ! grep -q "do not build the file up in pieces" "$REPO_ROOT/ai/lib/review-templates/single-agent.md"
}

@test "self-review.md turn-budget line allows complete rewrites" {
  grep -q "Further complete rewrites are expected" "$REPO_ROOT/ai/lib/review-templates/self-review.md"
  ! grep -q "do not build the file up in pieces" "$REPO_ROOT/ai/lib/review-templates/self-review.md"
}
