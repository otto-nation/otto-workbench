#!/usr/bin/env bats
# Tests for the Pi owner-boundary guard, $REPO_ROOT/ai/pi/extensions/upstream-guard.
# select-on: ai/pi/extensions/* ai/claude/bin/* ai/bin/* ai/lib/gh/*

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$HOME" "$WORKBENCH_CONFIG_DIR"
  unset GH_REPO GH_HOST WORKBENCH_ALLOW_UPSTREAM WORKBENCH_ALLOW_CROSS_REF
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _refusal CWD COMMAND [CHECKER] — upstreamRefusal's answer, or ALLOWED.
_refusal() {
  run node --input-type=module -e "
    const { upstreamRefusal } = await import('$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts');
    const reason = process.argv[3]
      ? upstreamRefusal(process.argv[2], process.argv[1], process.argv[3])
      : upstreamRefusal(process.argv[2], process.argv[1]);
    process.stdout.write(reason ?? 'ALLOWED');
  " -- "$1" "$2" "${3:-}"
}

# _tool_refusal CWD TOOL JSON [CHECKER] — toolCallRefusal's answer, or ALLOWED.
_tool_refusal() {
  run node --input-type=module -e "
    const { toolCallRefusal } = await import('$REPO_ROOT/ai/pi/extensions/upstream-guard/tool-calls.ts');
    const input = JSON.parse(process.argv[3]);
    const reason = process.argv[4]
      ? toolCallRefusal(process.argv[2], input, process.argv[1], process.argv[4])
      : toolCallRefusal(process.argv[2], input, process.argv[1]);
    process.stdout.write(reason ?? 'ALLOWED');
  " -- "$1" "$2" "$3" "${4:-}"
}

# _two_groups — github.owners with a work and a personal group.
_two_groups() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf 'github:\n  owners:\n    work: [otto-nation]\n    personal: [me]\n' > "$WORKBENCH_CONFIG_DIR/config.yml"
}

@test "upstream-guard: refuses a filing in a repo whose origin is not ours" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  _refusal "$TMPDIR/repo" 'gh issue create --title x'
  [ "$status" -eq 0 ]
  [[ "$output" == *"owner boundary"*"stranger"* ]]
  [[ "$output" == *"general.md § Upstream Repositories"* ]]
  [[ "$output" == *"for the user to run themselves"* ]]
  # The advice is the checker's reason alone; the guard's wrapper adds none.
  local rest="${output#*draft}"
  [[ "$rest" != *draft* ]]
}

@test "upstream-guard: allows a filing in our own repo" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  _refusal "$TMPDIR/repo" 'gh issue create --title x'
  [ "$output" = ALLOWED ]
}

@test "upstream-guard: refuses while github.owners is unset, naming the fix" {
  _upstream_repo git@github.com:otto-nation/svc.git
  _refusal "$TMPDIR/repo" 'gh pr comment 1 --body x'
  [[ "$output" == *"otto-workbench config set github.owners."* ]]
  # The fix is configuring the owners, not handing the write to the user.
  [[ "$output" != *"posts upstream"* && "$output" != *draft* ]]
}

@test "upstream-guard: a gh read in an upstream checkout is allowed by the check" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  _refusal "$TMPDIR/repo" 'gh pr view 1'
  [ "$output" = ALLOWED ]
}

@test "upstream-guard: a command naming no GitHub program never reaches the check" {
  _upstream_repo git@github.com:stranger/lib.git
  _refusal "$TMPDIR/repo" 'git log --oneline -3' "$TMPDIR/no-such-checker"
  [ "$output" = ALLOWED ]
}

@test "upstream-guard: refuses any command that names the override" {
  _refusal "$TMPDIR" 'echo WORKBENCH_ALLOW_UPSTREAM'
  [[ "$output" == *"override"* ]]
  [[ "$output" == *"WORKBENCH_ALLOW_UPSTREAM"* ]]
  [[ "$output" == *"general.md § Upstream Repositories"* ]]
}

@test "upstream-guard: refuses any command that names the cross-ref override" {
  _refusal "$TMPDIR" 'echo WORKBENCH_ALLOW_CROSS_REF'
  [[ "$output" == *"override"* ]]
  [[ "$output" == *"WORKBENCH_ALLOW_CROSS_REF"* ]]
  [[ "$output" == *"general.md § Cross-Group References"* ]]
}

@test "upstream-guard: refuses when the check cannot run" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  _refusal "$TMPDIR/repo" 'gh issue create --title x' "$TMPDIR/no-such-checker"
  [[ "$output" == *"might be a GitHub write, and the owner-boundary check could not run ("* ]]
  [[ "$output" == *"could not run"* ]]
}

@test "upstream-guard: refuses when the check crashes" {
  printf '#!/usr/bin/env bash\necho Traceback >&2\nexit 1\n' > "$TMPDIR/crashing-checker"
  chmod +x "$TMPDIR/crashing-checker"
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  _refusal "$TMPDIR/repo" 'gh issue create --title x' "$TMPDIR/crashing-checker"
  [[ "$output" == *"might be a GitHub write"*"exited 1"* ]]
}

@test "upstream-guard: a timeout reports the bound actually used" {
  printf '#!/usr/bin/env bash\nsleep 5\n' > "$TMPDIR/slow-checker"
  chmod +x "$TMPDIR/slow-checker"
  run node --input-type=module -e "
    const { upstreamRefusal } = await import('$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts');
    process.stdout.write(upstreamRefusal('gh issue create --title x', process.argv[1], process.argv[2], 300) ?? 'ALLOWED');
  " -- "$TMPDIR" "$TMPDIR/slow-checker"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no answer within 0.3s"* ]]
}

@test "upstream-guard: detect.ts imports no SDK, index.ts no shared module" {
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts"
  [ "$status" -ne 0 ]
  run grep -nE "from ['\"]\.\./_" "$REPO_ROOT/ai/pi/extensions/upstream-guard/index.ts"
  [ "$status" -ne 0 ]
}

@test "upstream-guard: TS, bash and Python spell the override variables the same" {
  local ts_up ts_cross python_up python_cross bash_block
  ts_up=$(node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts');
    process.stdout.write(m.ALLOW_UPSTREAM_ENV);
  ")
  ts_cross=$(node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts');
    process.stdout.write(m.ALLOW_CROSS_REF_ENV);
  ")
  python_up=$(python3 -c '
import sys; sys.path.insert(0, sys.argv[1])
import gh.owners; print(gh.owners.ALLOW_UPSTREAM_ENV)
' "$REPO_ROOT/ai/lib")
  python_cross=$(python3 -c '
import sys; sys.path.insert(0, sys.argv[1])
import gh.owners; print(gh.owners.ALLOW_CROSS_REF_ENV)
' "$REPO_ROOT/ai/lib")
  bash_block=$(awk '/^UPSTREAM_OVERRIDE_VARS=\(/{p=1} p{print} p && /^\)/{exit}' \
    "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ -n "$python_up" ]
  [ -n "$python_cross" ]
  [ "$ts_up" = "$python_up" ]
  [ "$ts_cross" = "$python_cross" ]
  [[ "$bash_block" == *"$python_up"* ]]
  [[ "$bash_block" == *"$python_cross"* ]]
}

@test "upstream-guard: both harnesses name both override variables" {
  local claude="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  local pi="$REPO_ROOT/ai/pi/extensions/upstream-guard/detect.ts"
  local name
  for name in WORKBENCH_ALLOW_UPSTREAM WORKBENCH_ALLOW_CROSS_REF; do
    grep -qF "$name" "$claude"
    grep -qF "$name" "$pi"
  done
}

@test "upstream-guard: Claude and Pi agree end to end on an upstream checkout" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  local cmd claude pi seen=""
  for cmd in 'gh issue create --title x' 'gh pr view 1' 'gh api -X POST repos/otto-nation/svc/issues' \
             'gh issue comment 1 --repo otto-nation/svc --body x' 'pr review 12' 'git status' \
             $'gh issue comment 1 --repo "a\nexit 0\n" --body x; gh issue create --repo stranger/lib --title pwn'; do
    if _json_command_payload_at "$TMPDIR/repo" "$cmd" \
        | (cd "$TMPDIR/repo" && "$REPO_ROOT/ai/claude/bin/claude-bash-guard" >/dev/null 2>&1); then
      claude=allowed
    else
      claude=refused
    fi
    _refusal "$TMPDIR/repo" "$cmd"
    if [ "$output" = ALLOWED ]; then pi=allowed; else pi=refused; fi
    [ "$claude" = "$pi" ] || { echo "disagree on: $cmd (claude=$claude pi=$pi)"; return 1; }
    seen+=" $pi"
  done
  # Agreement means nothing if both sides gave one answer to everything.
  [[ "$seen" == *allowed* && "$seen" == *refused* ]]
}

@test "upstream-guard: gh_pr_post_comment in an upstream checkout is refused" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  _tool_refusal "$TMPDIR/repo" gh_pr_post_comment '{"body":"hello"}'
  [[ "$output" == *"owner boundary"*"stranger"* ]]
}

@test "upstream-guard: gh_pr_reply_comment in an upstream checkout is refused" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  _tool_refusal "$TMPDIR/repo" gh_pr_reply_comment '{"reply_to_id":1,"body":"hello"}'
  [[ "$output" == *"stranger"* ]]
}

@test "upstream-guard: a PR comment naming another owner group is refused" {
  _two_groups
  _upstream_repo git@github.com:otto-nation/svc.git
  _tool_refusal "$TMPDIR/repo" gh_pr_post_comment '{"body":"see me/lib#12"}'
  [[ "$output" == *"me/lib#12"* ]]
}

@test "upstream-guard: one crossing body refuses a whole bulk reply" {
  _two_groups
  _upstream_repo git@github.com:otto-nation/svc.git
  _tool_refusal "$TMPDIR/repo" gh_pr_bulk_reply \
    '{"replies":[{"reply_to_id":1,"body":"fine"},{"reply_to_id":2,"body":"see me/lib#12"}]}'
  [[ "$output" == *"me/lib#12"* ]]
}

@test "upstream-guard: a plain PR comment in our own repo is allowed" {
  _two_groups
  _upstream_repo git@github.com:otto-nation/svc.git
  _tool_refusal "$TMPDIR/repo" gh_pr_post_comment '{"body":"looks good"}'
  [ "$output" = ALLOWED ]
  _tool_refusal "$TMPDIR/repo" gh_pr_bulk_reply \
    '{"replies":[{"reply_to_id":1,"body":"a"},{"reply_to_id":2,"body":"b"}]}'
  [ "$output" = ALLOWED ]
}

@test "upstream-guard: a tool that publishes nothing never reaches the check" {
  _tool_refusal "$TMPDIR" gh_pr_diff '{}' /nonexistent
  [ "$output" = ALLOWED ]
}

@test "upstream-guard: a publishing tool with no working check is refused" {
  _tool_refusal "$TMPDIR" gh_pr_post_comment '{"body":"hi"}' /nonexistent
  [[ "$output" == *"could not run"* ]]
}

@test "upstream-guard: a refused tool call leaves no temp directory behind" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  mkdir -p "$TMPDIR/scratch"
  TMPDIR="$TMPDIR/scratch" _tool_refusal "$TMPDIR/repo" gh_pr_post_comment '{"body":"hello"}'
  [[ "$output" == *"stranger"* ]]
  run find "$TMPDIR/scratch" -name 'upstream-guard-*'
  [ -z "$output" ]
}

@test "upstream-guard: tool-calls.ts imports no SDK" {
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/upstream-guard/tool-calls.ts"
  [ "$status" -ne 0 ]
  [ -f "$REPO_ROOT/ai/pi/extensions/upstream-guard/tool-calls.ts" ]
}
