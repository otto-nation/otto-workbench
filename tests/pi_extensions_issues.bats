#!/usr/bin/env bats
# Tests for the Pi issue guards: issue-defer, tree-lock, issue-capture, record-filed-issue, session-lock.
setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  # Around 150 cases below evaluate a guard predicate by spawning `node` to
  # import one .ts module, and nearly all of the ~82ms that costs is startup
  # and type-stripping rather than the predicate. Node's compile cache makes
  # that work survive across processes, taking it to ~59ms.
  #
  # $BATS_FILE_TMPDIR, not $TMPDIR: common_setup pins the latter per test, so a
  # cache written there is discarded before the next case can read it, and the
  # first-run cost would be paid every time. bats removes the file-level
  # directory when the file finishes, so nothing outlives the run.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# ── issue-defer-guard ────────────────────────────────────────────────────────
# Same split as the guards above: the predicate is in detect.ts, which imports
# nothing. Whether a review has open findings is a filesystem question index.ts
# asks, and the Claude guard's copy of it is covered in claude_settings.bats.

# _files_issue COMMAND — prints true or false for isIssueFiling(COMMAND).
_files_issue() {
  run node --input-type=module -e "
    const { isIssueFiling } = await import('$REPO_ROOT/ai/pi/extensions/issue-defer-guard/detect.ts');
    process.stdout.write(String(isIssueFiling(process.argv[1])));
  " -- "$1"
}

@test "issue-defer-guard: gh issue create is filing" {
  _files_issue 'gh issue create --title x --body-file /tmp/b.md'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: reads and other subcommands are not filing" {
  local cmd
  for cmd in 'gh issue view 1' 'gh issue list --state open' 'gh issue edit 3 --body x' 'gh pr create'; do
    _files_issue "$cmd"
    [ "$output" = false ] || {
      echo "matched a non-filing command: $cmd"
      return 1
    }
  done
}

@test "issue-defer-guard: the phrase inside another command is not filing" {
  # A guard that fired on any mention would block reading about itself.
  _files_issue 'echo gh issue create'
  [ "$output" = false ]

  _files_issue 'grep -rn "gh issue create" ai/'
  [ "$output" = false ]
}

@test "issue-defer-guard: a filing after a cd still counts" {
  _files_issue 'cd /tmp/repo && gh issue create --title x'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: a filing on a later line still counts" {
  # A bare `^`/`$` does not cross a literal newline, so a multi-line command —
  # the sanctioned form for a compound cd, per bash-tool.md § Avoid Compound
  # `cd` Commands — must not slip past on a raw whole-string match.
  _files_issue 'cd /tmp/repo
gh issue create --title x'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: agrees with the Claude guard command for command" {
  # Two guards enforcing one rule that disagree are worse than one guard: which
  # answer you get would depend on which harness you happen to be in. The
  # Claude side needs a repo with an open-findings review before its pattern is
  # reached, so build one (via the same helper claude_settings.bats uses) and
  # compare the pair on every shape.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  local cmd payload claude_blocks pi_blocks
  for cmd in \
    'gh issue create --title x' \
    'cd /tmp && gh issue create' \
    'echo gh issue create' \
    'grep -rn "gh issue create" ai/' \
    'gh issue view 1' \
    'gh issue list'; do

    payload=$(_json_command_payload "$cmd")

    if _guard_in "$sandbox" "$payload" > /dev/null 2>&1; then
      claude_blocks=false
    else
      claude_blocks=true
    fi

    _files_issue "$cmd"
    pi_blocks="$output"

    [ "$claude_blocks" = "$pi_blocks" ] || {
      echo "guards disagree on: $cmd (claude=$claude_blocks pi=$pi_blocks)"
      return 1
    }
  done
}

# _probe SANDBOX — branchReviewHasOpenFindings() as the Pi guard sees it, run
# from SANDBOX/repo with SANDBOX/state as the state root. The probe reads git
# and the filesystem, so unlike the predicate helpers above it needs a cwd.
_probe() {
  run node --input-type=module -e "
    process.chdir(process.argv[1] + '/repo');
    process.env.WORKBENCH_STATE_DIR = process.argv[1] + '/state';
    const { branchReviewHasOpenFindings } = await import('$REPO_ROOT/ai/pi/extensions/issue-defer-guard/detect.ts');
    process.stdout.write(String(branchReviewHasOpenFindings()));
  " -- "$1"
}

@test "issue-defer-guard: the probe sees open findings on the branch" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  _probe "$sandbox"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: the probe is quiet once every finding is ticked" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" "x")"

  _probe "$sandbox"
  [ "$output" = false ]
}

@test "issue-defer-guard: the probe fails open with no review, no branch, or no remote" {
  # Each is a state the probe cannot answer in. Blocking on any of them would
  # make the guard fire on repos it knows nothing about, which is worse than
  # the mistake it exists to prevent.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing")"
  _probe "$sandbox"
  [ "$output" = false ]

  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"
  git -C "$sandbox/repo" checkout -q --detach
  _probe "$sandbox"
  [ "$output" = false ]

  git -C "$sandbox/repo" checkout -q "isaac/fix/thing"
  git -C "$sandbox/repo" remote remove origin
  _probe "$sandbox"
  [ "$output" = false ]
}

@test "issue-defer-guard: both harnesses read the same review for one branch" {
  # The probe and _branch_review_has_open_findings build the review path
  # independently — same repo-from-remote, same branch slug, same state root.
  # A rename on either side makes that guard read an empty directory and fall
  # silent, so the two are held to one answer here.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  _probe "$sandbox"
  [ "$output" = true ]

  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 2 ]
}

# ── tree-lock-guard ──────────────────────────────────────────────────────────
# Same split as issue-defer-guard: the predicate is in detect.ts, which imports
# nothing but node builtins, so node can load it directly. index.ts is the
# wiring no test can reach.
#
# The probe is with-tree-lock --check, which is is_locked(). Tests hold a real
# flock rather than stubbing the CLI, so an inverted probe fails here the same
# way it fails in tests/tree_lock_test.py.

# _lock_refusal FILE — prints the refusal string, or "null".
_lock_refusal() {
  run node --input-type=module -e "
    const { lockRefusal } = await import('$REPO_ROOT/ai/pi/extensions/tree-lock-guard/detect.ts');
    const msg = lockRefusal(process.argv[1]);
    process.stdout.write(msg === null ? 'null' : msg);
  " -- "$1"
}

# _hold_tree lives in tests/test_helper.bash: claude_settings.bats holds the
# same lock for the Claude half of this guard, and one fact read by two
# harnesses is worth one helper rather than two copies of it.

@test "tree-lock-guard: a free tree is not a refusal" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  _lock_refusal "$repo/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "tree-lock-guard: a held tree is a refusal that names no pid" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local holder
  holder="$(_hold_tree "$repo")"
  _lock_refusal "$repo/file.txt"
  local result_status=$status result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$result_status" -eq 0 ]
  [[ "$result_out" == *"A validator holds this tree"* ]]
  [[ "$result_out" != *[Pp]id* ]]
}

@test "tree-lock-guard: WORKBENCH_TREE_LOCK does not suppress the refusal" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local holder
  holder="$(_hold_tree "$repo")"
  WORKBENCH_TREE_LOCK="$repo" _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
}

@test "tree-lock-guard: inherited GIT_DIR does not retarget the probe" {
  local repo="$TMPDIR/repo" other="$TMPDIR/other"
  mkdir -p "$repo" "$other"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  git -C "$other" init -q -b feat
  git -C "$other" config user.email t@t
  git -C "$other" config user.name t
  git -C "$other" commit -q --allow-empty -m init
  local holder other_git
  holder="$(_hold_tree "$repo")"
  other_git=$(git -C "$other" rev-parse --absolute-git-dir)
  GIT_DIR="$other_git" GIT_WORK_TREE="$other" _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
}

@test "tree-lock-guard: a torn record still refuses without naming a pid" {
  # holders() can be [] while is_locked() is true. Take LOCK_SH without
  # writing a JSONL line, which is the state acquire()'s _record can tear into.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local git_dir lock
  git_dir=$(git -C "$repo" rev-parse --absolute-git-dir)
  lock="$git_dir/workbench-validate.lock"
  python3 -c "
import fcntl, sys, time
h = open(sys.argv[1], 'a+')
fcntl.flock(h, fcntl.LOCK_SH)
time.sleep(30)
" "$lock" >/dev/null 2>&1 &
  local py=$! locked=""
  for _ in $(seq 1 50); do
    if "$REPO_ROOT/bin/local/with-tree-lock" --check "$repo" >/dev/null 2>&1; then
      locked=yes
      break
    fi
    sleep 0.1
  done
  # Fail here rather than at the assertion below: an unlocked tree makes
  # _lock_refusal correctly return null, and the final assertion would report
  # a guard that failed to refuse instead of a lock that was never taken.
  if [[ -z "$locked" ]]; then
    kill "$py" 2>/dev/null || true
    echo "tree $repo never became locked" >&2
    return 1
  fi
  _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$py" 2>/dev/null || true
  wait "$py" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
  [[ "$result_out" != *[Pp]id* ]]
}

@test "tree-lock-guard: missing path or non-git path fails open" {
  # The status is asserted alongside the output: a node crash whose message
  # happened to contain "null" would otherwise pass the output check alone.
  _lock_refusal "$TMPDIR/no-such-parent/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
  mkdir -p "$TMPDIR/plain"
  _lock_refusal "$TMPDIR/plain/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "tree-lock-guard: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/tree-lock-guard/detect.ts"
  [ "$status" -ne 0 ]
}

# ── issue-capture ────────────────────────────────────────────────────────────
#
# The recording half of the filing rule. Its refusing half is issue-defer-guard
# above; both harnesses have both, and ai/bin/record-filed-issue is the one
# writer they share.

# _captures COMMAND — prints true or false for isIssueFiling(COMMAND).
_captures() {
  run node --input-type=module -e "
    const { isIssueFiling } = await import('$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts');
    console.log(isIssueFiling(process.argv[1]));
  " "$1"
}

@test "issue-capture: a filing is recognised, a mention of one is not" {
  _captures 'gh issue create --title x'
  [ "$output" = true ]
  _captures 'cd /x && gh issue create'
  [ "$output" = true ]
  _captures 'echo gh issue create'
  [ "$output" = false ]
  _captures 'gh issue list'
  [ "$output" = false ]
}

@test "issue-capture: the two harnesses recognise the same filings" {
  # The Claude side is the same regex in the recorder and in the PreToolUse
  # guard. A command one harness records and the other does not is a filing
  # that reaches the ledger from one seat and not the other.
  local cmd
  for cmd in \
    'gh issue create --title x' \
    'echo gh issue create' \
    'gh issue list'; do
    _captures "$cmd"
    local pi="$output"
    _files_issue "$cmd"
    [ "$pi" = "$output" ] || {
      echo "capture and defer-guard disagree on: $cmd"
      echo "  issue-capture=$pi  issue-defer-guard=$output"
      return 1
    }
  done
}

@test "issue-capture: the bash regex and both TS matchers agree on the same commands" {
  # The two TS matchers are checked against each other above. The bash side
  # reached from Claude's PostToolUse hook (record-filed-issue's own
  # re_issue_create) and the PreToolUse guard (claude-bash-guard's copy of the
  # same variable) is a third, independent implementation of "is this a filing",
  # kept in sync with the other two only by a code comment — a bash-vs-TS drift
  # here is exactly the three-way drift this epic's guards used to suffer from,
  # and none of the tests above would catch it.
  local re_bash
  re_bash=$(grep -m1 "^re_issue_create=" "$REPO_ROOT/ai/bin/record-filed-issue")
  local re_guard
  re_guard=$(grep -m1 "^re_issue_create=" "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ "$re_bash" = "$re_guard" ]

  local cmd
  for cmd in \
    'gh issue create --title x' \
    'cd /x && gh issue create' \
    'echo gh issue create' \
    'gh issue list'; do
    # Sourced from the script rather than restated: a copy here would be a
    # fourth spelling, and the test would pass while the three real ones drift.
    # Unwrapped into a local of this test's own naming rather than eval'd into
    # the script's variable name, which shellcheck cannot see being assigned.
    local pattern="${re_bash#re_issue_create=}"
    pattern="${pattern#\'}"
    pattern="${pattern%\'}"
    local bash_result=false
    [[ "$cmd" =~ $pattern ]] && bash_result=true

    _captures "$cmd"
    local pi="$output"
    [ "$bash_result" = "$pi" ] || {
      echo "bash re_issue_create and issue-capture disagree on: $cmd"
      echo "  bash=$bash_result  issue-capture=$pi"
      return 1
    }
  done
}

@test "issue-capture: an ambiguous response records nothing rather than a guess" {
  # `gh issue create && gh issue view 5` prints two URLs, and picking one by
  # position records whichever the chain ended with — attributing someone
  # else's issue to this filing. A ledger entry pointing at the wrong issue is
  # worse than a missing one: it reads as a record somebody checked.
  #
  # The ledger is seeded first, and the assertion is that it still holds only
  # the seeded entry. Asserting "no state file" would pass on an empty sandbox
  # whatever the recorder did, which is no assertion at all.
  local sandbox="$BATS_TEST_TMPDIR/amb"
  mkdir -p "$sandbox/repo" "$sandbox/state"
  git -C "$sandbox/repo" init -q -b feat/amb
  git -C "$sandbox/repo" remote add origin git@github.com:otto-nation/otto-workbench.git
  git -C "$sandbox/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init

  run env WORKBENCH_STATE_DIR="$sandbox/state" python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + "/ai/lib")
from pathlib import Path
from pr import state as s, target as t
d = t.target_dir_for_checkout(Path(sys.argv[2]))
d.mkdir(parents=True, exist_ok=True)
s.save_state(d, s.new_state(repo="otto-nation/otto-workbench", branch="feat/amb",
                            pr_number=1, head_sha="abc", worktree_root=sys.argv[2]))
print(d)
' "$REPO_ROOT" "$sandbox/repo"
  [ "$status" -eq 0 ]
  local target="$output"

  local payload
  payload=$(python3 -c '
import json
print(json.dumps({
    "tool_input": {"command": "gh issue create --title x && gh issue view 5"},
    "tool_response": {"stdout": "https://github.com/o/r/issues/99\nhttps://github.com/o/r/issues/5\n"},
}))')

  run env WORKBENCH_STATE_DIR="$sandbox/state" \
    bash -c "cd '$sandbox/repo' && printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]

  # The seeded ledger is untouched: no entry, rather than an entry naming 5.
  run python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + "/ai/lib")
from pathlib import Path
from pr import state as s
st = s.load_state(Path(sys.argv[2]))
print(",".join(e.ref.id for e in st.follow_ups.entries))
' "$REPO_ROOT" "$target"
  [ "$output" = "" ]
}

@test "issue-capture: the url is read from what gh printed, or nothing is" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts');
    console.log(JSON.stringify([
      m.filedIssueUrl('https://github.com/o/r/issues/12'),
      m.filedIssueUrl('error: could not create issue'),
      m.issueIdFrom('https://github.com/o/r/issues/12'),
    ]));
  "
  [ "$output" = '["https://github.com/o/r/issues/12",null,"12"]' ]
}

@test "issue-capture: detect.ts imports no SDK" {
  # Same contract as the other predicates: bats loads this under bare node.
  #
  # Matched on `import` lines rather than anywhere in the file. The older twins
  # of this test grep the whole text, which also fires on a header comment that
  # merely explains why the SDK is absent — a file cannot document the rule it
  # obeys without failing the check for it.
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts"
  [ "$status" -ne 0 ]
}

@test "issue-capture: the recorder is handed its payload on stdin" {
  # `execFile` has no `input` option. Passing one leaves the recorder blocked on
  # a stdin that never closes and the hook hangs — which is not a crash, so
  # nothing reports it: the filing simply never reaches the ledger. Asserted on
  # the wiring because the failure is invisible in the extension's own output.
  local index="$REPO_ROOT/ai/pi/extensions/issue-capture/index.ts"
  grep -q 'child.stdin?.end(' "$index"
  run grep -nE 'execFile\([^)]*\binput:' "$index"
  [ "$status" -ne 0 ]

  # And the real shape round-trips: a payload written to stdin reaches a reader
  # that blocks on `cat`, and the callback fires.
  run node --input-type=module -e "
    import { execFile } from 'node:child_process';
    const child = execFile('bash', ['-c', 'cat'], { timeout: 5000 },
      (err, stdout) => { console.log(err ? 'ERR' : stdout.trim()); });
    child.stdin.end('{\"ok\":1}');
  "
  [ "$output" = '{"ok":1}' ]
}

@test "issue-capture: both harnesses write through the one recorder" {
  # Two copies of "what a ledger entry looks like" would drift the way the
  # guards did before they shared a scan.
  grep -q 'record-filed-issue' "$REPO_ROOT/ai/pi/extensions/issue-capture/index.ts"
  grep -q 'record-filed-issue' "$REPO_ROOT/ai/claude/settings.json"
}

# ── record-filed-issue ───────────────────────────────────────────────────────
#
# The shared writer both harnesses hand a payload to. Exercised directly here
# (rather than through either extension) because the URL-attribution question
# below is about the writer's own parsing, not about either hook's wiring.

_record_filed_issue_setup() {
  RFI_REPO="$TMPDIR/rfi-repo"
  mkdir -p "$RFI_REPO"
  git -C "$RFI_REPO" init -q -b main
  git -C "$RFI_REPO" remote add origin git@github.com:acme/widget.git
  git -C "$RFI_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init

  RFI_TARGET=$(python3 - "$REPO_ROOT" "$RFI_REPO" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target
print(pr_target.target_dir_for_checkout(Path(sys.argv[2])))
PY
)
  mkdir -p "$RFI_TARGET"
  cat > "$RFI_TARGET/state.json" <<EOF
{"_version": 1, "identity": {"repo": "acme/widget", "branch": "main", "pr_number": null, "head_sha": "abc123", "worktree_root": "$RFI_REPO"}, "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z"}
EOF
}

_rfi_entry_count() {
  python3 -c "
import json
with open('$RFI_TARGET/state.json') as f:
    d = json.load(f)
print(len(d.get('follow_ups', {}).get('entries', [])))
"
}

@test "record-filed-issue: a single issue URL is recorded" {
  _record_filed_issue_setup
  cd "$RFI_REPO"
  payload='{"tool_input":{"command":"gh issue create --title x"},"tool_response":{"stdout":"https://github.com/acme/widget/issues/42"}}'
  run bash -c "printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]
  [ "$(_rfi_entry_count)" = "1" ]
  grep -q '"id": "42"' "$RFI_TARGET/state.json"
}

@test "record-filed-issue: more than one URL-shaped match records nothing rather than guess" {
  # Nothing here tells the hook which of the two matches this `gh issue
  # create` actually filed, so it must not guess with `tail -1` and risk
  # attributing the wrong issue to this filing.
  _record_filed_issue_setup
  cd "$RFI_REPO"
  payload='{"tool_input":{"command":"gh issue create --title x"},"tool_response":{"stdout":"https://github.com/acme/widget/issues/42\nhttps://github.com/acme/widget/issues/99"}}'
  run bash -c "printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]
  [ "$(_rfi_entry_count)" = "0" ]
}

# ── session-lock ─────────────────────────────────────────────────────────────
# Same split as tree-lock-guard: the claim is in detect.ts, which imports one
# node builtin, so node can load it directly. index.ts is the wiring no test
# can reach.
#
# The record these write is read by ai/lib/fix/engine.py to refuse a pass, and
# by ai/claude/bin/claude-session-lock for the other harness. Tests record a
# real live pid rather than stubbing the CLI, so a renamed binary or a moved
# lock path fails here rather than silently never claiming.

# _session_claim REPO PID MODE — acquire or release through detect.ts.
_session_claim() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/session-lock/detect.ts');
    const pid = Number(process.argv[2]);
    const out = process.argv[3] === 'release'
      ? m.releaseFor(process.argv[1], pid)
      : m.acquireFor(process.argv[1], pid, 'sess-1');
    process.stdout.write(String(out));
  " -- "$1" "$2" "${3:-acquire}"
}

@test "session-lock: a claim makes the worktree read as being edited" {
  local repo="$TMPDIR/sl-repo"
  mkdir -p "$repo"
  git -C "$repo" init -q

  # A process that outlives the node run: the record is only live while its
  # pid is, so claiming node's own pid would prune itself on the next read.
  sleep 30 &
  local holder=$!

  _session_claim "$repo" "$holder"
  [ "$output" = "true" ]

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 0 ]
  [[ "$output" == *"pid $holder"* ]]

  kill "$holder" 2>/dev/null || true
}

@test "session-lock: releasing clears the claim" {
  local repo="$TMPDIR/sl-release"
  mkdir -p "$repo"
  git -C "$repo" init -q

  sleep 30 &
  local holder=$!

  _session_claim "$repo" "$holder"
  _session_claim "$repo" "$holder" release
  [ "$output" = "true" ]

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 1 ]

  kill "$holder" 2>/dev/null || true
}

@test "session-lock: a killed session stops holding the worktree" {
  # Kill-safety without a daemon, which is the whole reason the claim is a
  # record rather than a flock. `wait` is the synchronisation point.
  local repo="$TMPDIR/sl-killed"
  mkdir -p "$repo"
  git -C "$repo" init -q

  sleep 30 &
  local holder=$!
  _session_claim "$repo" "$holder"

  kill -9 "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 1 ]
}

@test "session-lock: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/session-lock/detect.ts"
  [ "$status" -ne 0 ]
}
