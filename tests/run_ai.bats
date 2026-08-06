#!/usr/bin/env bats
# Coverage for run_ai's ledger plumbing (_ai_unwrap, _ai_record) in lib/ai/core.sh.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_DIR="$PWD"
  ORIG_PATH="$PATH"
  TMPDIR="$(mktemp -d)"
  cd "$TMPDIR"
}

teardown() {
  cd "$ORIG_DIR"
  PATH="$ORIG_PATH"
  rm -rf "$TMPDIR"
  common_teardown
}

# make_fake_ai_cmd NAME EXIT_CODE — stub AI_COMMAND binary that prints a fixed
# reply and exits with EXIT_CODE.
make_fake_ai_cmd() {
  local name="$1"
  local exit_code="${2:-0}"
  mkdir -p "$TMPDIR/bin"
  cat > "$TMPDIR/bin/$name" << SCRIPT
#!/bin/bash
cat > /dev/null
echo "reply text"
exit $exit_code
SCRIPT
  chmod +x "$TMPDIR/bin/$name"
}

# make_fake_usage_log — stub ai-usage-log that tees on unwrap and records its
# record-subcommand arguments to RECORD_ARGS_FILE for assertions.
make_fake_usage_log() {
  mkdir -p "$TMPDIR/bin"
  RECORD_ARGS_FILE="$TMPDIR/record-args.txt"
  cat > "$TMPDIR/bin/ai-usage-log" << SCRIPT
#!/bin/bash
case "\$1" in
  unwrap)
    shift
    tee_file=""
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        --tee) tee_file="\$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ -n "\$tee_file" ]]; then
      tee "\$tee_file"
    else
      cat
    fi
    ;;
  record)
    shift
    printf '%s\n' "\$@" > "$RECORD_ARGS_FILE"
    ;;
esac
SCRIPT
  chmod +x "$TMPDIR/bin/ai-usage-log"
}

@test "run_ai tees the raw response and records usage when ai-usage-log is present" {
  make_fake_ai_cmd fake-ai 0
  make_fake_usage_log
  AI_COMMAND="fake-ai"
  PATH="$TMPDIR/bin:$PATH"

  run_ai "prompt" "" "my-task"

  [[ "$AI_RESPONSE" == "reply text" ]]
  [ -f "$RECORD_ARGS_FILE" ]
  grep -q -- "--task" "$RECORD_ARGS_FILE"
  grep -q -- "my-task" "$RECORD_ARGS_FILE"
}

@test "run_ai records the AI command's real exit code, not a hardcoded 0" {
  make_fake_ai_cmd fake-ai 1
  make_fake_usage_log
  AI_COMMAND="fake-ai"
  PATH="$TMPDIR/bin:$PATH"

  run_ai "prompt" "" "my-task"

  [ -f "$RECORD_ARGS_FILE" ]
  grep -q -- "--exit-code" "$RECORD_ARGS_FILE"
  grep -A1 -- "--exit-code" "$RECORD_ARGS_FILE" | grep -q "^1$"
}

@test "run_ai falls back to plain output when ai-usage-log is absent" {
  make_fake_ai_cmd fake-ai 0
  AI_COMMAND="fake-ai"
  PATH="$TMPDIR/bin:$PATH"

  run_ai "prompt" "" "my-task"

  [[ "$AI_RESPONSE" == "reply text" ]]
}
