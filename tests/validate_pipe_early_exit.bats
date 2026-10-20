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

_check() {
  local f
  f=$(_write_script "case.sh")
  run "$VALIDATE" "$f"
}

@test "flags a multi-line awk program that exits" {
  _check <<'S'
#!/usr/bin/env bash
cmd | awk '
  /x/ { print
    exit }
'
S
  [ "$status" -eq 1 ]
}

@test "flags -F and -v flags before the program" {
  _check <<'S'
#!/usr/bin/env bash
cmd | awk -F'\t' -v want=1 '$1 == want { print; exit }'
S
  [ "$status" -eq 1 ]
}

@test "accepts || awk (not a pipeline)" {
  _check <<'S'
#!/usr/bin/env bash
cmd || awk '{ print; exit }'
S
  [ "$status" -eq 0 ]
}

@test "ignores a comment mentioning | awk exit" {
  _check <<'S'
#!/usr/bin/env bash
# cmd | awk '{ print; exit }'
echo ok
S
  [ "$status" -eq 0 ]
}

@test "flags awk exit followed by a 2> redirect" {
  _check <<'S'
#!/usr/bin/env bash
cmd | awk '{ print; exit }' 2>/dev/null
S
  [ "$status" -eq 1 ]
}

@test "flags awk exit reading stdin via dash" {
  _check <<'S'
#!/usr/bin/env bash
cmd | awk '{ print; exit }' -
S
  [ "$status" -eq 1 ]
}

@test "ignores heredoc prose that mentions | awk exit" {
  _check <<'S'
#!/usr/bin/env bash
cat <<EOF
usage: cmd | awk '{ print; exit }'
EOF
S
  [ "$status" -eq 0 ]
}

@test "here-string and quoted << do not start a heredoc" {
  _check <<'S'
#!/usr/bin/env bash
echo "a <<EOF" > /dev/null
cat <<< word
cmd | awk '{ print; exit }'
EOF
S
  [ "$status" -eq 1 ]
}

@test "accepts awk exit with dash followed by a file" {
  _check <<'S'
#!/usr/bin/env bash
cmd | awk '{ print; exit }' - file.txt
S
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
