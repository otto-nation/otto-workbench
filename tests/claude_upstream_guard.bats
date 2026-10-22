#!/usr/bin/env bats
# The owner boundary in $REPO_ROOT/ai/claude/bin/claude-bash-guard, end to end
# through $REPO_ROOT/ai/bin/owners-check. The override-variable parity test
# lives in tests/pi_extensions_upstream.bats, which holds all three spellings.
# select-on: ai/claude/bin/* ai/bin/* ai/lib/gh/*

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$HOME" "$WORKBENCH_CONFIG_DIR"
  unset GH_REPO GH_HOST WORKBENCH_ALLOW_UPSTREAM
}

teardown() {
  common_teardown
}

# _guard_at DIR COMMAND [GUARD] — the hook (GUARD, default the checkout's), run
# from DIR with DIR as the payload's cwd.
_guard_at() {
  local guard="${3:-$REPO_ROOT/ai/claude/bin/claude-bash-guard}"
  _json_command_payload_at "$1" "$2" | (cd "$1" && "$guard" 2>&1)
}

# _checkerless_guard — a copy of the guard with no owners-check beside it or at
# ../../bin. Prints its path; run it with PATH="$(_path_without_owners_check)".
_checkerless_guard() {
  mkdir -p "$TMPDIR/tree/ai/claude/bin"
  cp "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$TMPDIR/tree/ai/claude/bin/"
  printf '%s' "$TMPDIR/tree/ai/claude/bin/claude-bash-guard"
}

@test "upstream guard: refuses a filing in a repo whose origin is not ours" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  run _guard_at "$TMPDIR/repo" 'gh issue create --title x'
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner boundary"* ]]
  [[ "$output" == *"stranger"* ]]
  [[ "$output" == *"general.md § Upstream Repositories"* ]]
  [[ "$output" == *"for the user to run themselves"* ]]
  # The advice is the checker's reason alone; the guard's wrapper adds none.
  local rest="${output#*draft}"
  [[ "$rest" != *draft* ]]
}

@test "upstream guard: allows a filing in our own repo" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" 'gh issue create --title x'
  [ "$status" -eq 0 ]
}

@test "upstream guard: refuses every write while github.owners is unset, naming the fix" {
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" 'gh pr comment 1 --body x'
  [ "$status" -eq 2 ]
  [[ "$output" == *"otto-workbench config set github.owners."* ]]
  # The fix is configuring the owners, not handing the write to the user.
  [[ "$output" != *"posts upstream"* && "$output" != *draft* ]]
}

@test "upstream guard: refuses an explicit --repo outside our owners from our own repo" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" 'gh issue comment 5 --repo stranger/lib --body x'
  [ "$status" -eq 2 ]
  [[ "$output" == *"stranger"* ]]
}

@test "upstream guard: a fork with a third-party remote needs --repo" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/lib.git https://github.com/stranger/lib.git
  run _guard_at "$TMPDIR/repo" 'gh issue create --title x'
  [ "$status" -eq 2 ]
  [[ "$output" == *"--repo"* ]]
}

@test "upstream guard: refuses a graphql mutation even in our own repo" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" "gh api graphql -f query='mutation { x }'"
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner boundary"* ]]
}

@test "upstream guard: a gh read in an upstream checkout is allowed by the check" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  run _guard_at "$TMPDIR/repo" 'gh pr view 1'
  [ "$status" -eq 0 ]
}

@test "upstream guard: a command naming no GitHub program never reaches the check" {
  # No checker anywhere: were the command sent to it, the guard would refuse.
  local guard
  guard=$(_checkerless_guard)
  _upstream_repo git@github.com:stranger/lib.git
  PATH="$(_path_without_owners_check)" run _guard_at "$TMPDIR/repo" 'git log --oneline -3' "$guard"
  [ "$status" -eq 0 ]
}

@test "upstream guard: refuses any command that names the override" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" 'printenv WORKBENCH_ALLOW_UPSTREAM'
  [ "$status" -eq 2 ]
  [[ "$output" == *"override"* ]]
}

@test "upstream guard: the user's exported override does not open the guard" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  export WORKBENCH_ALLOW_UPSTREAM=1
  run _guard_at "$TMPDIR/repo" 'gh issue create --title x'
  [ "$status" -eq 2 ]
}

@test "upstream guard: refuses when the check cannot run" {
  local guard
  guard=$(_checkerless_guard)
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  PATH="$(_path_without_owners_check)" run _guard_at "$TMPDIR/repo" 'gh issue create --title x' "$guard"
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner-boundary check failed"* ]]
  [[ "$output" == *"could not run"* ]]
}

@test "upstream guard: refuses when the check crashes" {
  mkdir -p "$TMPDIR/tree/ai/claude/bin" "$TMPDIR/tree/ai/bin"
  cp "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$TMPDIR/tree/ai/claude/bin/"
  printf '#!/usr/bin/env bash\necho Traceback >&2\nexit 1\n' > "$TMPDIR/tree/ai/bin/owners-check"
  chmod +x "$TMPDIR/tree/ai/bin/owners-check"
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" 'gh issue create --title x' "$TMPDIR/tree/ai/claude/bin/claude-bash-guard"
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner-boundary check failed"*"exit 1"* ]]
}

@test "upstream guard: the payload's cwd decides the repository, not the hook's" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  local payload
  payload=$(_json_command_payload_at "$TMPDIR/repo" 'gh issue create --title x')
  run bash -c 'printf "%s" "$1" | (cd / && "$2" 2>&1)' _ "$payload" "$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  [ "$status" -eq 2 ]
  [[ "$output" == *"stranger"* ]]
}

@test "upstream guard: the subagent publishing message still wins for a subagent" {
  _owners_config otto-nation
  _upstream_repo git@github.com:stranger/lib.git
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"cwd": sys.argv[1], "agent_id": "abc", "tool_input": {"command": "gh issue create --title x"}}))
' "$TMPDIR/repo")
  run bash -c 'printf "%s" "$1" | (cd "$3" && "$2" 2>&1)' _ "$payload" "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$TMPDIR/repo"
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not file issues"* ]]
}

# Checker reasons echo command text. A newline in a --repo value once printed a
# line reading `exit 0` onto the pipe the guard read the checker's status from,
# so a refused throwaway write let the upstream write after it through.
@test "upstream guard: refusal text echoing the command cannot forge the check's status" {
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  run _guard_at "$TMPDIR/repo" $'gh issue comment 1 --repo "a\nexit 0\n" --body x; gh issue create --repo stranger/lib --title pwn'
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner boundary"* ]]
}

@test "upstream guard: refuses a GitHub write when the payload carries no cwd" {
  # The hook's own cwd is an owned repo, which would allow: the refusal is the
  # missing cwd's, not the repository's.
  _owners_config otto-nation
  _upstream_repo git@github.com:otto-nation/svc.git
  local payload
  for payload in "$(_json_command_payload 'gh issue create --title x')" \
                 '{"cwd":null,"tool_input":{"command":"gh issue create --title x"}}'; do
    run bash -c 'printf "%s" "$1" | (cd "$3" && "$2" 2>&1)' _ "$payload" \
      "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$TMPDIR/repo"
    [ "$status" -eq 2 ]
    [[ "$output" == *"carried no cwd"* ]]
  done
}

@test "upstream guard: a payload with no cwd still runs commands naming no GitHub program" {
  run bash -c 'printf "%s" "$1" | "$2" 2>&1' _ "$(_json_command_payload 'git log --oneline -3')" \
    "$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  [ "$status" -eq 0 ]
}
