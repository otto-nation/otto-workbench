#!/usr/bin/env bats
# Tests for validate-pipe-early-exit — an early-exit consumer (awk exit,
# head, grep -q/-m/-l, sed q) on a pipeline SIGPIPEs the producer under
# pipefail.

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

@test "flags git log piped to head" {
  _check <<'S'
#!/usr/bin/env bash
git log | head -1
S
  [ "$status" -eq 1 ]
  [[ "$output" == *"capture"* ]]
}

@test "flags find piped to grep -q" {
  _check <<'S'
#!/usr/bin/env bash
find . | grep -q x
S
  [ "$status" -eq 1 ]
}

@test "flags git ls-files piped to grep -qE" {
  _check <<'S'
#!/usr/bin/env bash
git ls-files | grep -qE x
S
  [ "$status" -eq 1 ]
}

@test "flags ps piped to sed that quits" {
  _check <<'S'
#!/usr/bin/env bash
ps | sed -n '1p;q'
S
  [ "$status" -eq 1 ]
}

@test "flags grep -m1 as an early-exit consumer" {
  _check <<'S'
#!/usr/bin/env bash
cmd | grep -m1 x
S
  [ "$status" -eq 1 ]
}

@test "accepts printf piped to grep -q" {
  _check <<'S'
#!/usr/bin/env bash
printf '%s\n' "$v" | grep -q x
S
  [ "$status" -eq 0 ]
}

@test "accepts echo piped to head" {
  _check <<'S'
#!/usr/bin/env bash
echo "$v" | head -1
S
  [ "$status" -eq 0 ]
}

@test "accepts grep -q on a here-string" {
  _check <<'S'
#!/usr/bin/env bash
grep -q x <<<"$out"
S
  [ "$status" -eq 0 ]
}

@test "accepts grep without an early-exit flag" {
  _check <<'S'
#!/usr/bin/env bash
cmd | grep x
S
  [ "$status" -eq 0 ]
}

@test "ignores cmd | head inside a heredoc body" {
  _check <<'S'
#!/usr/bin/env bash
cat <<EOF
usage: cmd | head -1
EOF
S
  [ "$status" -eq 0 ]
}

@test "ignores a comment mentioning | head" {
  _check <<'S'
#!/usr/bin/env bash
# git log | head -1
echo ok
S
  [ "$status" -eq 0 ]
}

@test "flags head inside a quoted command substitution" {
  _check <<'S'
#!/usr/bin/env bash
since_date="$(git log --reverse | head -1)"
S
  [ "$status" -eq 1 ]
}

@test "accepts echo piped to grep -q inside a function body" {
  _check <<'S'
#!/usr/bin/env bash
_is_allowlisted() { echo "$1" | grep -qw "$2"; }
S
  [ "$status" -eq 0 ]
}
