#!/usr/bin/env bats

load test_helper

setup() {
  common_setup
  WITH_LOCK="$REPO_ROOT/bin/local/with-tree-lock"
  TREE="$BATS_TEST_TMPDIR/tree"
  git init -q "$TREE"
}

@test "with-tree-lock runs the command and passes its exit status" {
  run "$WITH_LOCK" "$TREE" -- true
  [ "$status" -eq 0 ]

  run "$WITH_LOCK" "$TREE" -- sh -c 'exit 7'
  [ "$status" -eq 7 ]
}

@test "with-tree-lock does not eat the child command's own flags" {
  # argparse must never see the child command: as a positional it claims
  # leading flags and dies on "unrecognized arguments". The extra --flag
  # tokens belong to the child (sh -c ignores them after the script).
  run "$WITH_LOCK" "$TREE" -- sh -c 'exit 0' -- --version --flag
  [ "$status" -eq 0 ]
  [[ "$output" != *"unrecognized arguments"* ]]
}

@test "a child killed by a signal reports the shell's status convention" {
  run "$WITH_LOCK" "$TREE" -- sh -c 'kill -TERM $$'
  [ "$status" -eq 143 ]
  run "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 1 ]
}

@test "killing the holder stops the child's descendants" {
  # The pid --check prints is the Python wrapper. Signalling it must take the
  # child's process group with it, or the tree reads as free while work is
  # still running. Poll rather than sleep: spawn and reap are both racy on a
  # loaded runner, and a fixed window would pass a slow signal as success.
  local pidfile="$BATS_TEST_TMPDIR/grandchild.pid"
  local i holder grandchild
  "$WITH_LOCK" "$TREE" -- sh -c "sleep 30 & echo \$! > \"$pidfile\"; wait" &
  local wrapper=$!

  holder=""
  for i in $(seq 1 50); do
    if [[ -f "$pidfile" ]]; then
      run "$WITH_LOCK" --check "$TREE"
      if [[ "$status" -eq 0 ]]; then
        holder=$(printf '%s\n' "$output" | awk '/^  pid / { print $2; exit }')
        [[ -n "$holder" ]] && break
      fi
    fi
    sleep 0.1
  done
  [[ -n "$holder" ]]
  grandchild=$(cat "$pidfile")
  kill -0 "$grandchild"

  kill -TERM "$holder"

  for i in $(seq 1 50); do
    kill -0 "$grandchild" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$grandchild" 2>/dev/null; then
    kill -9 "$grandchild" 2>/dev/null || true
    printf 'grandchild %s survived kill of holder %s\n' "$grandchild" "$holder" >&2
    wait "$wrapper" || true
    return 1
  fi

  wait "$wrapper" || true
  run "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 1 ]
}

@test "holder records do not accumulate across runs" {
  "$WITH_LOCK" "$TREE" -- true
  "$WITH_LOCK" "$TREE" -- true
  run "$WITH_LOCK" "$TREE" --label "bin/local/run-tests" -- "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | grep -c 'pid ')" -eq 1 ]
}

@test "with-tree-lock holds the lock for the child's lifetime" {
  run "$WITH_LOCK" "$TREE" -- "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"validating"* ]]
}

@test "the lock is released once the command finishes" {
  "$WITH_LOCK" "$TREE" -- true
  run "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not being validated"* ]]
}

@test "--check names the holding command" {
  run "$WITH_LOCK" "$TREE" --label "bin/local/run-tests" -- "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin/local/run-tests"* ]]
}

@test "the lock is released when the command fails" {
  run "$WITH_LOCK" "$TREE" -- false
  [ "$status" -eq 1 ]
  run "$WITH_LOCK" --check "$TREE"
  [ "$status" -eq 1 ]
}

@test "two trees lock independently" {
  local other="$BATS_TEST_TMPDIR/other"
  git init -q "$other"
  run "$WITH_LOCK" "$TREE" -- "$WITH_LOCK" --check "$other"
  [ "$status" -eq 1 ]
}

@test "a command outside a git repo still runs" {
  local plain="$BATS_TEST_TMPDIR/plain"
  mkdir -p "$plain"
  run "$WITH_LOCK" "$plain" -- true
  [ "$status" -eq 0 ]
}

@test "with-tree-lock rejects a missing command" {
  run "$WITH_LOCK" "$TREE"
  [ "$status" -ne 0 ]
  [[ "$output" == *"--"* ]]
}

@test "with-tree-lock documents usage" {
  run "$WITH_LOCK" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"with-tree-lock"* ]]
}
