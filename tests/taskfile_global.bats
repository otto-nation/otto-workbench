#!/usr/bin/env bats
# Tests for Taskfile.global.yml structure and env configuration.
# Validates that go-task integration works correctly.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# ─── YAML syntax ────────────────────────────────────────────────────────────

@test "Taskfile.global.yml is valid YAML" {
  run yq '.' "$REPO_ROOT/Taskfile.global.yml"
  [ "$status" -eq 0 ]
}

# ─── env block — TASKFILE_DIR export ─────────────────────────────────────────

@test "Taskfile.global.yml exports TASKFILE_DIR as env var" {
  local val
  val=$(yq '.env.TASKFILE_DIR' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$val" == '{{.TASKFILE_DIR}}' ]]
}

# ─── core.sh sourcing — TASKFILE_DIR context (simulates go-task sh -c) ──────

@test "core.sh resolves conventions.sh via TASKFILE_DIR" {
  # Simulates the go-task execution path: sh -c with TASKFILE_DIR set
  run sh -c "TASKFILE_DIR='$REPO_ROOT' . '$REPO_ROOT/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

@test "core.sh sets COMMIT_HEADER_MAX_LEN via conventions.sh" {
  # Validates the full sourcing chain: core.sh → conventions.sh → constants
  run bash -c ". '$REPO_ROOT/lib/ai/core.sh' && [[ \$COMMIT_HEADER_MAX_LEN -gt 0 ]] && echo ok"
  [ "$status" -eq 0 ]
  [[ "$output" == "ok" ]]
}

# ─── core.sh sourcing — BASH_SOURCE context (bin scripts) ───────────────────

@test "core.sh resolves conventions.sh via BASH_SOURCE" {
  run bash -c ". '$REPO_ROOT/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

# ─── Symlinked TASKFILE_DIR (global task install path) ───────────────────────

@test "core.sh works when sourced through symlinked TASKFILE_DIR" {
  # Simulates ~/.config/task/ setup: Taskfile.yml and lib/ are symlinks
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run sh -c "TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

@test "WORKBENCH_ROOT reaches ai/ through a symlinked TASKFILE_DIR" {
  # The install links lib/ into the checkout and nothing else, so a root taken
  # from TASKFILE_DIR itself finds conventions.sh and no ai/ script at all —
  # which is how pr:create came to invoke an ai/lib/git/push.py that wasn't there.
  #
  # dash, not sh: on macOS /bin/sh is bash, which sets BASH_SOURCE even under
  # `sh -c`, so `sh -c` here would exercise the BASH_SOURCE branch instead of
  # the TASKFILE_DIR branch this test exists to cover.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

# ─── All lib/ai sourcing goes through the pin ────────────────────────────────

@test "every lib/ai source line in a task body goes through \$WORKBENCH_LIB_DIR" {
  # Supersedes a count of '{{.TASKFILE_DIR}}/lib/ai/core.sh' lines against a
  # count of 'lib/ai/core.sh' lines. The property is the same one — no source
  # line escapes the indirection — but asserted over every lib/ai module
  # rather than core.sh alone, because a hardcoded path on any one of them
  # pins that module to the installed checkout while its siblings follow
  # WORKBENCH_LIB_DIR, and a branch's core.sh over main's compact_diff.sh is a
  # build neither tree has.
  #
  # yq over the task bodies, not grep over the raw YAML: the env entry that
  # validates the pin names lib/ai/core.sh twice and is not a source line, so
  # any count taken over the whole file now measures the guard as well.
  local bad
  bad=$(yq -r '.tasks[].cmds[]' "$REPO_ROOT/Taskfile.global.yml" \
    | grep -E '^[[:space:]]*\.[[:space:]]' \
    | grep '/lib/ai/' \
    | grep -v '^[[:space:]]*\. "\$WORKBENCH_LIB_DIR/lib/ai/') || true
  [ -z "$bad" ]
}

@test "the global Taskfile still sources lib/ai through the pin" {
  # Vacuity guard for the test above: a yq path that stopped matching, or a
  # grep that stopped selecting, would let it pass having examined nothing.
  local n
  n=$(yq -r '.tasks[].cmds[]' "$REPO_ROOT/Taskfile.global.yml" \
    | grep -c '^[[:space:]]*\. "\$WORKBENCH_LIB_DIR/lib/ai/') || true
  [ "$n" -gt 0 ]
}

# ─── WORKBENCH_LIB_DIR — opt-in library pin ──────────────────────────────────

@test "Taskfile.global.yml defaults WORKBENCH_LIB_DIR to TASKFILE_DIR" {
  # Same two properties as before the guard moved out of the env entry: the
  # default is TASKFILE_DIR, and an override is checked against lib/ai/core.sh.
  # They are now asserted where each one lives — resolution in the env entry,
  # checking in the guard task — rather than in one `sh:` string holding both.
  local resolved guard
  resolved=$(yq -r '.env.WORKBENCH_LIB_DIR' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$resolved" == *'{{.WORKBENCH_LIB_DIR | default .TASKFILE_DIR}}'* ]]

  guard=$(yq -r '.tasks."_lib-dir-guard".cmds[]' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$guard" == *'lib/ai/core.sh'* ]]
}

@test "the WORKBENCH_LIB_DIR env entry splices nothing into a shell" {
  # The S1 property, asserted structurally so it cannot regress into a
  # differently-quoted splice: the env entry is a plain template string with no
  # `sh:` at all, so there is no script text for a value to break out of. A
  # value reaches the guard through the environment only.
  local entry
  entry=$(yq -r '.env.WORKBENCH_LIB_DIR | type' "$REPO_ROOT/Taskfile.global.yml")
  [ "$entry" = '!!str' ]

  # And the guard body names the variable, never a {{...}} substitution — a
  # template rendered into these lines would be spliced text again.
  local guard
  guard=$(yq -r '.tasks."_lib-dir-guard".cmds[]' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$guard" == *'"$WORKBENCH_LIB_DIR"'* ]]
  [[ "$guard" != *'{{'* ]]
}

@test "every task that sources lib/ai depends on the guard" {
  # The guard moved from a Taskfile-level env entry to `deps:`, so coverage is
  # now per-task and a new task could source lib/ai without one. Asserted as a
  # property over the task bodies rather than a list of names.
  local bad
  bad=$(yq -r '.tasks | to_entries[]
    | select(.key != "_lib-dir-guard")
    | select((.value.cmds // [] | tostring) | contains("lib/ai/"))
    | select(((.value.deps // []) | tostring) | contains("_lib-dir-guard") | not)
    | .key' "$REPO_ROOT/Taskfile.global.yml")
  [ -z "$bad" ]
}

@test "the guard covers every task that sources lib/ai" {
  # Vacuity guard for the test above, which passes when it selects nothing --
  # a yq path that stopped matching would take every task out of scope at once.
  # Counted against the tasks that source lib/ai rather than a literal, so a
  # legitimate new one moves both sides and only a missing `deps:` fails.
  local guarded sourcing
  guarded=$(yq -r '.tasks | to_entries[]
    | select(((.value.deps // []) | tostring) | contains("_lib-dir-guard"))
    | .key' "$REPO_ROOT/Taskfile.global.yml" | grep -c .) || true
  sourcing=$(yq -r '.tasks | to_entries[]
    | select(.key != "_lib-dir-guard")
    | select((.value.cmds // [] | tostring) | contains("lib/ai/"))
    | .key' "$REPO_ROOT/Taskfile.global.yml" | grep -c .) || true

  [ "$sourcing" -gt 0 ]
  [ "$guarded" -eq "$sourcing" ]
}

@test "tasks that load no library carry no guard" {
  # The N2 decision, asserted: validation sits at the point of consumption, so
  # ai:setup and brew:dump neither fork a shell for the guard nor fail on a pin
  # they never read.
  local deps
  for t in ai:setup brew:dump help; do
    deps=$(yq -r ".tasks.\"$t\".deps // [] | tostring" "$REPO_ROOT/Taskfile.global.yml")
    [[ "$deps" != *'_lib-dir-guard'* ]]
  done
}

@test "core.sh prefers WORKBENCH_LIB_DIR over TASKFILE_DIR" {
  # dash for the same reason as the test above: /bin/sh is bash on macOS and
  # would take the BASH_SOURCE branch, which deliberately ignores the pin.
  # TASKFILE_DIR is pointed somewhere unusable, so a WORKBENCH_ROOT that still
  # reaches ai/ proves the pin was the value that resolved it.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "TASKFILE_DIR='/nonexistent' WORKBENCH_LIB_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

@test "core.sh falls back to TASKFILE_DIR when WORKBENCH_LIB_DIR is unset" {
  # The default path, asserted directly: an unset pin must resolve exactly what
  # TASKFILE_DIR resolved before the variable existed.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "unset WORKBENCH_LIB_DIR; TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

@test "core.sh ignores WORKBENCH_LIB_DIR when BASH_SOURCE resolves the path" {
  # A stale pin in an interactive environment must not redirect a bash caller
  # away from the file it just sourced.
  run bash -c "WORKBENCH_LIB_DIR='/nonexistent' . '$REPO_ROOT/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ "$output" = "$REPO_ROOT" ]
}

@test "commit.sh and review.sh honour the same pin as core.sh" {
  # Neither has a runnable test for its fallback branch: compact_diff.sh uses
  # `local chunks=()`, which dash cannot parse, and bash always sets
  # BASH_SOURCE — so that branch only ever executes under go-task's shell.
  # A textual assertion is the honest cover, and it catches the regression
  # that matters: a module left on TASKFILE_DIR while core.sh follows the pin.
  grep -q '${WORKBENCH_LIB_DIR:-${TASKFILE_DIR' "$REPO_ROOT/lib/ai/commit.sh"
  grep -q '${WORKBENCH_LIB_DIR:-${TASKFILE_DIR' "$REPO_ROOT/lib/ai/review.sh"
}

@test "a bogus WORKBENCH_LIB_DIR fails the task by name" {
  command -v task >/dev/null 2>&1 || skip "task not installed"
  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=$BATS_TEST_TMPDIR/nope" commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"WORKBENCH_LIB_DIR"* ]]
}

@test "a relative WORKBENCH_LIB_DIR is refused" {
  command -v task >/dev/null 2>&1 || skip "task not installed"
  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=relative/path" commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"absolute path"* ]]
}

@test "a WORKBENCH_LIB_DIR containing a quote executes nothing" {
  # The S1 regression. The value closes the single-quoted assignment the old
  # env entry spliced it into and appends a command; under that entry the rest
  # of the script parsed as loose shell tokens and `touch` ran. The value now
  # reaches the guard through the environment, so it is a path and nothing else.
  command -v task >/dev/null 2>&1 || skip "task not installed"
  local canary="$BATS_TEST_TMPDIR/PWNED"

  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=/tmp/x'; touch $canary; d='" commit
  [ "$status" -ne 0 ]
  [ ! -e "$canary" ]
  # Fails by name, as a path — not as a syntax error from a broken assignment.
  [[ "$output" == *"WORKBENCH_LIB_DIR"* ]]
  [[ "$output" != *"unexpected"* ]]
}

@test "a WORKBENCH_LIB_DIR with a backtick or \$( executes nothing" {
  # The same property for command substitution: neither form is evaluated,
  # because nothing expands the value as script text.
  command -v task >/dev/null 2>&1 || skip "task not installed"
  local canary="$BATS_TEST_TMPDIR/SUBBED"

  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=/tmp/\$(touch $canary)\`touch $canary\`" commit
  [ "$status" -ne 0 ]
  [ ! -e "$canary" ]
  [[ "$output" == *"WORKBENCH_LIB_DIR"* ]]
}

@test "a partial checkout is refused by the missing path's name" {
  # N1. lib/ai/core.sh alone passed the old gate, and the run then failed
  # inside python3 on an ai/lib/git/push.py that was never there, with nothing
  # naming the variable that sent it. Each path a task reaches is checked, and
  # the message names the one that is missing.
  command -v task >/dev/null 2>&1 || skip "task not installed"
  local partial="$BATS_TEST_TMPDIR/partial"
  mkdir -p "$partial/lib/ai"
  cp "$REPO_ROOT/lib/ai/core.sh" "$partial/lib/ai/core.sh"
  cp "$REPO_ROOT/lib/conventions.sh" "$partial/lib/conventions.sh"
  cp "$REPO_ROOT/lib/config_cli.py" "$partial/lib/config_cli.py"

  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=$partial" commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"ai/lib/git/push.py"* ]]
  [[ "$output" == *"WORKBENCH_LIB_DIR"* ]]
}

@test "a real checkout passes the guard" {
  # The other half of N1: the check must not be so strict that a legitimate
  # worktree fails it. The guard body is read out of the Taskfile and run under
  # sh with the environment go-task would give it — the task itself is
  # internal:true and cannot be invoked by name, and every task that carries it
  # in deps: goes on to do real work once it passes.
  #
  # Both trees, because a path added to the guard that only one of them happens
  # to have would otherwise pass here and fail every default run.
  local main_dir
  main_dir=$(cd "$REPO_ROOT/../main" 2>/dev/null && pwd -P) || skip "no sibling main/ checkout"

  local guard tree
  guard=$(yq -r '.tasks."_lib-dir-guard".cmds[0]' "$REPO_ROOT/Taskfile.global.yml")

  for tree in "$REPO_ROOT" "$main_dir"; do
    run env "WORKBENCH_LIB_DIR=$tree" "TASKFILE_DIR=$main_dir" sh -c "$guard"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
  done
}

@test "an unset pin short-circuits the guard before any filesystem check" {
  # Unset resolves to TASKFILE_DIR, so the guard's first line must return
  # before it stats anything — the default path stays byte-identical even when
  # TASKFILE_DIR names a directory that does not exist.
  local guard
  guard=$(yq -r '.tasks."_lib-dir-guard".cmds[0]' "$REPO_ROOT/Taskfile.global.yml")

  run env WORKBENCH_LIB_DIR=/nonexistent TASKFILE_DIR=/nonexistent sh -c "$guard"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "the default path passes the guard from the installed symlink farm" {
  # The short-circuit is load-bearing, not an optimisation. The installed
  # TASKFILE_DIR is ~/.config/task, which holds a `lib` link and no ai/ at all,
  # so an unset pin that fell through to the loop would fail every default run
  # on the machine. core.sh resolves the default through that link to the real
  # checkout; the farm itself is never the root being checked.
  local guard fake_task_dir
  guard=$(yq -r '.tasks."_lib-dir-guard".cmds[0]' "$REPO_ROOT/Taskfile.global.yml")
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run env "WORKBENCH_LIB_DIR=$fake_task_dir" "TASKFILE_DIR=$fake_task_dir" sh -c "$guard"
  [ "$status" -eq 0 ]
  [ -z "$output" ]

  # And pinning *at* the farm is still refused — it is the root whose missing
  # push.py broke pr:create, so it must not pass as an explicit override.
  run env "WORKBENCH_LIB_DIR=$fake_task_dir" TASKFILE_DIR=/elsewhere sh -c "$guard"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ai/lib/git/push.py"* ]]
}
