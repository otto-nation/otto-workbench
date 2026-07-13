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

@test "_build_agent_cmd: includes max-turns when set" {
  result=$(_py '
import ai_backend_claude as abc
cmd = abc._build_agent_cmd(add_dirs=["/tmp/reviews", "/tmp/wt"], max_turns=10)
print("--max-turns" in cmd, cmd[cmd.index("--max-turns") + 1] if "--max-turns" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"10"* ]]
}

@test "_build_agent_cmd: includes max-budget-usd when set" {
  result=$(_py '
import ai_backend_claude as abc
cmd = abc._build_agent_cmd(add_dirs=["/tmp/reviews", "/tmp/wt"], max_budget=5.0)
print("--max-budget-usd" in cmd, cmd[cmd.index("--max-budget-usd") + 1] if "--max-budget-usd" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"5.0"* ]]
}

@test "_build_agent_cmd: omits flags when None" {
  result=$(_py '
import ai_backend_claude as abc
cmd = abc._build_agent_cmd(add_dirs=["/tmp/reviews", "/tmp/wt"])
print("--max-turns" not in cmd and "--max-budget-usd" not in cmd)
')
  [ "$result" = "True" ]
}

@test "_build_agent_cmd: includes model when set" {
  result=$(_py '
import ai_backend_claude as abc
cmd = abc._build_agent_cmd(add_dirs=["/tmp/reviews", "/tmp/wt"], model="sonnet")
print("--model" in cmd, cmd[cmd.index("--model") + 1] if "--model" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"sonnet"* ]]
}


# ── review-rebuild smoke tests ───────────────────────────────────────────────

@test "review-rebuild: rebuilds review.md from group files" {
  # Get section headers from severity registry
  must_section=$(_py "print(mod.severity_by_key('M').section)")
  should_section=$(_py "print(mod.severity_by_key('S').section)")
  must_prefix="M"
  should_prefix="S"

  mkdir -p "$TMPDIR/review"
  cat > "$TMPDIR/review/group-1.md" <<EOF
## $must_section
- **[${must_prefix}1]** **\`foo.py:10\`** — missing error check
EOF
  cat > "$TMPDIR/review/group-2.md" <<EOF
## $should_section
- **[${should_prefix}1]** **\`bar.py:5\`** — unused import
EOF
  cat > "$TMPDIR/review/meta.json" <<'EOF'
{"repo":"org/repo","pr_number":"42","head_sha":"abc123","head_ref":"feat/test","base_ref":"main","review_type":"full","title":"Test PR","changed_files":3,"mode":"pr"}
EOF

  "$REPO_ROOT/ai/claude/bin/review-rebuild" \
    --review-dir "$TMPDIR/review" --pr 42

  [ -f "$TMPDIR/review/review.md" ]
  grep -q "# Review: org/repo#42 — Test PR" "$TMPDIR/review/review.md"
  grep -q "## $must_section" "$TMPDIR/review/review.md"
  grep -q "## $should_section" "$TMPDIR/review/review.md"
  grep -q "## Verdict" "$TMPDIR/review/review.md"
  grep -q "2 groups" "$TMPDIR/review/review.md"
}

@test "review-rebuild: self-review mode omits verdict" {
  nit_section=$(_py "print(mod.severity_by_key('N').section)")
  nit_prefix="N"

  mkdir -p "$TMPDIR/review"
  cat > "$TMPDIR/review/group-1.md" <<EOF
## $nit_section
- **[${nit_prefix}1]** **\`foo.py:1\`** — style nit
EOF
  cat > "$TMPDIR/review/meta.json" <<'EOF'
{"repo":"org/repo","pr_number":"","head_sha":"abc","head_ref":"feat/self","base_ref":"main","review_type":"full","mode":"self"}
EOF

  "$REPO_ROOT/ai/claude/bin/review-rebuild" \
    --review-dir "$TMPDIR/review" --pr ""

  grep -q "# Self-Review: org/repo — feat/self" "$TMPDIR/review/review.md"
  ! grep -q "## Verdict" "$TMPDIR/review/review.md"
}

@test "review-rebuild: no group files exits with error" {
  mkdir -p "$TMPDIR/empty"
  cat > "$TMPDIR/empty/meta.json" <<'EOF'
{"repo":"org/repo"}
EOF
  run "$REPO_ROOT/ai/claude/bin/review-rebuild" \
    --review-dir "$TMPDIR/empty" --pr 1
  [ "$status" -ne 0 ]
}

@test "review-rebuild: works with minimal meta.json" {
  must_section=$(_py "print(mod.severity_by_key('M').section)")
  must_prefix="M"

  mkdir -p "$TMPDIR/review"
  cat > "$TMPDIR/review/group-1.md" <<EOF
## $must_section
- **[${must_prefix}1]** **\`foo.py:10\`** — bug
EOF
  cat > "$TMPDIR/review/meta.json" <<'EOF'
{"repo":"org/repo"}
EOF

  "$REPO_ROOT/ai/claude/bin/review-rebuild" \
    --review-dir "$TMPDIR/review" --pr 99

  [ -f "$TMPDIR/review/review.md" ]
  grep -q "# Review: org/repo#99" "$TMPDIR/review/review.md"
  grep -q "1 finding" "$TMPDIR/review/review.md"
}

@test "review-rebuild: fails without meta.json" {
  must_section=$(_py "print(mod.severity_by_key('M').section)")
  must_prefix="M"

  mkdir -p "$TMPDIR/no-meta"
  cat > "$TMPDIR/no-meta/group-1.md" <<EOF
## $must_section
- **[${must_prefix}1]** **\`foo.py:1\`** — bug
EOF

  run "$REPO_ROOT/ai/claude/bin/review-rebuild" \
    --review-dir "$TMPDIR/no-meta" --pr 1
  [ "$status" -ne 0 ]
}

# ── Pi backend: RPC mode and budget ──────────────────────────────────────────

@test "pi _build_agent_cmd: uses --mode rpc" {
  result=$(_py '
import ai_backend_pi as abp
cmd = abp._build_agent_cmd()
print("--mode" in cmd, cmd[cmd.index("--mode") + 1] if "--mode" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"rpc"* ]]
}

@test "pi _build_agent_cmd: includes --thinking when set" {
  result=$(_py '
import ai_backend_pi as abp
cmd = abp._build_agent_cmd(thinking_level="medium")
print("--thinking" in cmd, cmd[cmd.index("--thinking") + 1] if "--thinking" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"medium"* ]]
}

@test "pi _build_agent_cmd: omits --thinking when None" {
  result=$(_py '
import ai_backend_pi as abp
cmd = abp._build_agent_cmd()
print("--thinking" not in cmd)
')
  [ "$result" = "True" ]
}

@test "pi _build_fix_cmd: includes --thinking when set" {
  result=$(_py '
import ai_backend_pi as abp
cmd = abp._build_fix_cmd(thinking_level="low")
print("--thinking" in cmd, cmd[cmd.index("--thinking") + 1] if "--thinking" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"low"* ]]
}

@test "claude _build_agent_cmd: accepts thinking_level without error" {
  result=$(_py '
import ai_backend_claude as abc
cmd = abc._build_agent_cmd(add_dirs=["/tmp"], thinking_level="high")
print(type(cmd).__name__, "--thinking" not in cmd)
')
  [[ "$result" == *"list"* ]]
  [[ "$result" == *"True"* ]]
}

@test "parse_pi_cost: extracts cost from message_end" {
  result=$(_py '
from ai_backend_events import parse_pi_cost
import json
line = json.dumps({"type": "message_end", "message": {"usage": {"cost": {"input": 0.01, "output": 0.05, "total": 0.06}}}})
print(f"{parse_pi_cost(line):.2f}")
')
  [ "$result" = "0.06" ]
}

@test "parse_pi_cost: returns None for non-message_end" {
  result=$(_py '
from ai_backend_events import parse_pi_cost
import json
line = json.dumps({"type": "turn_end"})
print(parse_pi_cost(line))
')
  [ "$result" = "None" ]
}

@test "parse_pi_cost: returns None for missing cost" {
  result=$(_py '
from ai_backend_events import parse_pi_cost
import json
line = json.dumps({"type": "message_end", "message": {}})
print(parse_pi_cost(line))
')
  [ "$result" = "None" ]
}

@test "parse_pi_cost: returns None for invalid JSON" {
  result=$(_py '
from ai_backend_events import parse_pi_cost
print(parse_pi_cost("not json"))
')
  [ "$result" = "None" ]
}

@test "pi _write_result_record: generates Claude-compatible record" {
  result=$(_py "
import ai_backend_pi as abp
import json
abp._write_result_record(
    '$TMPDIR/result.jsonl', 'completed', 5, 1.23, 60000,
    {'cost': 1.25, 'tokens': {'input': 1000, 'output': 500, 'cacheRead': 200, 'cacheWrite': 100}},
)
with open('$TMPDIR/result.jsonl') as f:
    rec = json.loads(f.read().strip())
print(rec['type'], rec['subtype'], f'{rec[\"total_cost_usd\"]:.2f}', rec['num_turns'])
print(rec['usage']['input_tokens'], rec['usage']['output_tokens'])
print(rec['usage']['cache_read_input_tokens'], rec['usage']['cache_creation_input_tokens'])
")
  [[ "$result" == *"result success 1.25 5"* ]]
  [[ "$result" == *"1000 500"* ]]
  [[ "$result" == *"200 100"* ]]
}

@test "pi _write_result_record: uses accumulated cost when stats empty" {
  result=$(_py "
import ai_backend_pi as abp
import json
abp._write_result_record(
    '$TMPDIR/fallback.jsonl', 'max_turns', 10, 3.50, 120000, {},
)
with open('$TMPDIR/fallback.jsonl') as f:
    rec = json.loads(f.read().strip())
print(f'{rec[\"total_cost_usd\"]:.2f}', rec['subtype'], rec['num_turns'])
")
  [ "$result" = "3.50 max_turns 10" ]
}

@test "pi _write_result_record: result record works with _parse_session_cost" {
  _py "
import ai_backend_pi as abp
abp._write_result_record(
    '$TMPDIR/compat.jsonl', 'completed', 3, 2.00, 30000,
    {'cost': 2.10, 'tokens': {'input': 500, 'output': 200}},
)
"
  result=$(_py "
cost = mod._parse_session_cost('$TMPDIR/compat.jsonl')
print(f'{cost:.2f}')
")
  [ "$result" = "2.10" ]
}
