#!/usr/bin/env bats
# Tests for the Pi job-poll-guard.
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

# ─── job-poll-guard ────────────────────────────────────────────────────────
# Refuses a second read of a job this agent run already polled. Same split as
# the other guards: detect.ts imports nothing, so every branch runs under a
# bare node.
#
# The predicate cannot ask whether a job is running — the jobs tools are
# another repo's, their manager is closure-local, and tool_call fires before
# execute. So the tests drive the sequence the guard actually sees: calls, and
# the text their results came back with.

# _poll OPS — run a sequence against one state and print each pollRefusal.
#
# OPS is a JSON array of steps, each one of:
#   {"call": "job_output", "id": "job-1"}   a guarded call; prints REFUSED or ok
#   {"result": "job_output", "id": "job-1", "text": "...", "isError": false}
#   {"reset": true}                          what agent_start does
_poll() {
  run node --input-type=module -e "
    const d = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    const state = d.freshState();
    const out = [];
    for (const step of JSON.parse(process.argv[1])) {
      if (step.reset) { d.resetState(state); continue; }
      if (step.result) {
        d.noteResult(state, step.result, step.text ?? '', step.isError ?? false);
        continue;
      }
      const reason = d.pollRefusal(state, step.call, step.id);
      if (reason === null) { d.noteCall(state, step.call, step.id); out.push('ok'); }
      else out.push('REFUSED');
    }
    process.stdout.write(out.join(','));
  " -- "$1"
}

_running_line() { printf '%s  [running 12s]  a job\n\nsome output' "$1"; }
_exited_line() { printf '%s  [exit 0 after 12s]  a job\n\nsome output' "$1"; }

@test "job-poll-guard: the first job_output of a run is allowed" {
  _poll '[{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "job-poll-guard: a repeat with no result yet is refused" {
  # Two job_output calls in one parallel batch both reach tool_call before
  # either result lands. That is the repeat with the least excuse.
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: polling a different job is not a repeat" {
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-2"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: a still-running result refuses the next read" {
  local line
  line=$(_running_line job-1)
  _poll "$(printf '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":%s},{"call":"job_output","id":"job-1"}]' "$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a finished job may be read again for more output" {
  # The tool's own description says to raise max_bytes for more, so a second
  # read of a job that has exited is the documented use, not a poll.
  local line
  line=$(_exited_line job-1)
  _poll "$(printf '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":%s},{"call":"job_output","id":"job-1"}]' "$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: a killed or failed job reads as finished" {
  _poll '[{"call":"job_output","id":"j"},{"result":"job_output","text":"j  [killed after 3s]  x"},{"call":"job_output","id":"j"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
  _poll '[{"call":"job_output","id":"j"},{"result":"job_output","text":"j  [failed after 3s]  x"},{"call":"job_output","id":"j"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: an unparseable result fails open" {
  # The line format belongs to another repo. A guard that refused on text it
  # could not read would block every poll the day that format changed.
  _poll '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":"something else entirely"},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: an errored result does not refuse the corrected retry" {
  # `No such job: x` is a typo, not a poll.
  _poll '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":"No such job: job-1","isError":true},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: the first job_list is allowed and a bare repeat is not" {
  _poll '[{"call":"job_list"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a list showing a running job refuses the next list" {
  _poll '[{"call":"job_list"},{"result":"job_list","text":"2 job(s), 1 running:\nj  [running 4s]  x"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a list with nothing running may be repeated" {
  _poll '[{"call":"job_list"},{"result":"job_list","text":"No background jobs."},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
  _poll '[{"call":"job_list"},{"result":"job_list","text":"2 job(s), 0 running:\nj  [exit 0 after 4s]  x"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: the two tools do not count against each other" {
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_list"},{"call":"job_output","id":"job-2"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok,ok" ]
}

@test "job-poll-guard: a reset allows a job refused before it" {
  # This is the agent_start contract. A job's completion is delivered as
  # nextTurn, so the read that follows the notice lands in a later agent run
  # and must not be refused as a repeat.
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-1"},{"reset":true},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED,ok" ]
}

# _parse_out TEXT / _parse_list TEXT — the two text readers, as JSON.
_parse_out() {
  run node --input-type=module -e "
    const { parseOutputStatus } = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    process.stdout.write(JSON.stringify(parseOutputStatus(process.argv[1])));
  " -- "$1"
}

_parse_list() {
  run node --input-type=module -e "
    const { parseListRunningCount } = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    process.stdout.write(JSON.stringify(parseListRunningCount(process.argv[1])));
  " -- "$1"
}

@test "job-poll-guard: the status reader matches formatJobLine's real shapes" {
  # Shapes taken from formatJobLine in usemaximum/pi-extensions extensions/jobs.
  _parse_out "job-1  [running 12s]  run the suite"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-1","status":"running"}' ]
  _parse_out "job-2  [exit 0 after 198s]  run the suite"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-2","status":"done"}' ]
  _parse_out "job-3  [killed after 3s]  x"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-3","status":"done"}' ]
  _parse_out "no brackets here"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "job-poll-guard: the list reader counts what is running" {
  _parse_list "3 job(s), 2 running:"
  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
  _parse_list "No background jobs."
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
  _parse_list "something else"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "job-poll-guard: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts"
  [ "$status" -ne 0 ]
}

@test "job-poll-guard: index.ts resets on agent_start, not turn_start" {
  # A turn_start reset would only catch two polls in one assistant message and
  # would refuse the legitimate read that follows a completion notice.
  run grep -q 'pi.on("agent_start"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'resetState(state)' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  # Scoped to a subscription, since the header comment explains at length why
  # turn_start is the wrong event and would match a bare grep for the word.
  run grep -q 'pi.on("turn_start"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -ne 0 ]
}

@test "job-poll-guard: index.ts decides through the predicate and records results" {
  # A hand-written reason at the call site would pass the refusal tests above
  # while the wiring bypassed the predicate entirely.
  run grep -q 'pollRefusal(state' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'pi.on("tool_result"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'noteResult(' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
}
