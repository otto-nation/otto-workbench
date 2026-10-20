#!/usr/bin/env bats
# Tests for validate-pipe-early-exit — awk that exits while consuming a
# pipeline SIGPIPEs the producer under pipefail.

setup() {
  load 'test_helper'
  common_setup
  VALIDATE="$REPO_ROOT/bin/local/validate-pipe-early-exit"
}

teardown() {
  common_teardown
}

_write_script() {
  local name="$1"
  local path="$TMPDIR/$name"
  cat > "$path"
  echo "$path"
}

@test "flags a pipeline awk that exits after the first match" {
  local f
  f=$(_write_script "early-exit.sh" <<'SCRIPT'
#!/usr/bin/env bash
git worktree list --porcelain | awk '/^worktree / { print; exit }'
SCRIPT
  )
  run "$VALIDATE" "$f"
  [ "$status" -eq 1 ]
  [[ "$output" == *"awk exit in a pipeline"* ]]
  [[ "$output" == *"SIGPIPE"* ]]
}

@test "accepts a pipeline awk that drains and prints the first match" {
  local f
  f=$(_write_script "drain.sh" <<'SCRIPT'
#!/usr/bin/env bash
git worktree list --porcelain | awk '/^worktree / { if (!found) { print; found = 1 } }'
SCRIPT
  )
  run "$VALIDATE" "$f"
  [ "$status" -eq 0 ]
}

@test "accepts awk exit when reading a file argument" {
  local f
  f=$(_write_script "file-arg.sh" <<'SCRIPT'
#!/usr/bin/env bash
awk 'NR==1 { print; exit }' "$topic_file"
SCRIPT
  )
  run "$VALIDATE" "$f"
  [ "$status" -eq 0 ]
}
