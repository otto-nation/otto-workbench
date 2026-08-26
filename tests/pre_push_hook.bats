#!/usr/bin/env bats
# Tests for the global pre-push hook: the repo-local hook it delegates to, and
# the push intent it records afterwards.
#
# The hook is exercised by real pushes between throwaway repositories rather
# than by calling it directly. Both things under test here are arrangements git
# makes and not the hook — the ref lines on stdin, and the fact that a non-zero
# exit refuses the push — so a hand-run hook would assert the harness instead.
#
# A lab of its own each time: GIT_CONFIG_GLOBAL points at a temp gitconfig whose
# core.hooksPath holds a symlink to this checkout's hook, exactly as
# step_global_hooks installs it. Nothing here can reach the developer's own hook
# path, state root, or repositories.

setup() {
  load 'test_helper'
  common_setup
  SANDBOX="$(mktemp -d)"
  # Exported on purpose, and the sandboxing is the point: git, python3 and jq
  # all take their scratch space from TMPDIR, so every subprocess a test starts
  # is confined to this directory alongside everything the test writes itself.
  export TMPDIR="$SANDBOX"
  sandbox_state_dir
  INTENTS="$WORKBENCH_STATE_DIR/push-intents.json"

  mkdir -p "$TMPDIR/hooks"
  ln -sf "$REPO_ROOT/git/hooks/pre-push" "$TMPDIR/hooks/pre-push"

  # The nesting gate below is about which validate-nesting the hook finds, so it
  # has to be this checkout's rather than whichever one the machine installed —
  # on a machine with none, `command -v` would skip the gate and a case asserting
  # a refusal would pass for the wrong reason.
  PATH="$REPO_ROOT/bin:$PATH"

  export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
  git config --global core.hooksPath "$TMPDIR/hooks"
  git config --global user.name "t"
  git config --global user.email "t@t"
  git config --global init.defaultBranch main
  git config --global commit.gpgsign false

  git init -q --bare -b main "$TMPDIR/remote.git"
  git init -q -b main "$TMPDIR/wt"
  git -C "$TMPDIR/wt" commit -q --allow-empty -m init
  git -C "$TMPDIR/wt" remote add origin "$TMPDIR/remote.git"
}

teardown() {
  rm -rf "$SANDBOX"
  common_teardown
}

# branches_recorded — the branch of every recorded intent, one per line.
branches_recorded() {
  jq -r '.intents[].branch' "$INTENTS" 2>/dev/null
}

# write_local_hook BODY — an executable .git/hooks/pre-push in the worktree.
write_local_hook() {
  mkdir -p "$TMPDIR/wt/.git/hooks"
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$TMPDIR/wt/.git/hooks/pre-push"
  chmod +x "$TMPDIR/wt/.git/hooks/pre-push"
}

# commit_deep_script NAME — commit a bash script nested past the depth limit.
commit_deep_script() {
  printf '%s\n' '#!/usr/bin/env bash' 'f() {' '  if true; then' \
    '    if true; then' '      if true; then' '        echo deep' \
    '      fi' '    fi' '  fi' '}' > "$TMPDIR/wt/$1"
  git -C "$TMPDIR/wt" add "$1"
  git -C "$TMPDIR/wt" commit -q -m "add $1"
}

# ── Nesting gate ─────────────────────────────────────────────────────────────

@test "a push carrying new excessive nesting is refused" {
  git -C "$TMPDIR/wt" push -q origin main
  commit_deep_script deep.sh
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -ne 0 ]
  [[ "$output" == *"nesting exceeds"* ]]
}

@test "a clean push is allowed over nesting that predates it" {
  # The bug this covers: measuring the whole tree refuses every later push in a
  # repository that already carries a violation, however unrelated. The offender
  # reaches the remote with the hook bypassed, standing in for debt that landed
  # before the gate existed.
  git -C "$TMPDIR/wt" push -q origin main
  commit_deep_script legacy.sh
  git -C "$TMPDIR/wt" -c core.hooksPath=/dev/null push -q origin main

  git -C "$TMPDIR/wt" commit -q --allow-empty -m "unrelated"
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
}

@test "a new branch is measured against the default branch, not the whole tree" {
  # A branch the remote has never seen arrives with an all-zero remote sha, so
  # there is no pushed base to diff against and the fallback decides whether the
  # repository's existing debt is charged to it.
  git -C "$TMPDIR/wt" push -q origin main
  commit_deep_script legacy.sh
  git -C "$TMPDIR/wt" -c core.hooksPath=/dev/null push -q origin main

  git -C "$TMPDIR/wt" checkout -q -b feature
  git -C "$TMPDIR/wt" commit -q --allow-empty -m "clean work"
  run git -C "$TMPDIR/wt" push origin feature
  [ "$status" -eq 0 ]
}

@test "a branch deletion is not measured for nesting" {
  git -C "$TMPDIR/wt" push -q origin main:doomed
  commit_deep_script legacy.sh
  git -C "$TMPDIR/wt" -c core.hooksPath=/dev/null push -q origin main

  run git -C "$TMPDIR/wt" push origin :doomed
  [ "$status" -eq 0 ]
}

# ── Recording ────────────────────────────────────────────────────────────────

@test "a push with no repo-local hook is recorded" {
  run git -C "$TMPDIR/wt" push -q origin main
  [ "$status" -eq 0 ]
  run branches_recorded
  [ "$output" = "main" ]
}

@test "a foo:bar push records the branch the remote moves" {
  # Neither HEAD nor a local branch named `bar` moves here, so anything but the
  # hook's stdin would record a ref the push never sent.
  git -C "$TMPDIR/wt" checkout -q -b foo
  git -C "$TMPDIR/wt" commit -q --allow-empty -m foo
  run git -C "$TMPDIR/wt" push -q origin foo:bar
  [ "$status" -eq 0 ]
  run branches_recorded
  [ "$output" = "bar" ]
  run jq -r '.intents[0].refspec' "$INTENTS"
  [ "$output" = "refs/heads/foo:refs/heads/bar" ]
}

@test "a multi-ref push records every ref it carries" {
  git -C "$TMPDIR/wt" branch -q one
  git -C "$TMPDIR/wt" branch -q two
  run git -C "$TMPDIR/wt" push -q origin one two
  [ "$status" -eq 0 ]
  run branches_recorded
  [ "$output" = "one
two" ]
}

@test "a delete drops the record for that branch" {
  git -C "$TMPDIR/wt" push -q origin main:doomed
  run branches_recorded
  [ "$output" = "doomed" ]

  run git -C "$TMPDIR/wt" push -q origin :doomed
  [ "$status" -eq 0 ]
  [ ! -f "$INTENTS" ]
}

@test "a tag push records nothing" {
  # `ls-remote --heads` has no answer for a tag, so a record would guarantee a
  # report that the push was lost.
  git -C "$TMPDIR/wt" tag v1
  run git -C "$TMPDIR/wt" push -q origin v1
  [ "$status" -eq 0 ]
  [ ! -f "$INTENTS" ]
}

@test "the push is recorded against the worktree it was made from" {
  git -C "$TMPDIR/wt" push -q origin main
  run jq -r '.intents[0].repo' "$INTENTS"
  # macOS resolves the temp root through /private, which the hook's own
  # rev-parse reports and the recorded path therefore carries.
  [ "${output##*/}" = "wt" ]
}

# ── Repo-local delegation ────────────────────────────────────────────────────

@test "a repo-local hook still receives the ref lines on stdin" {
  # The global hook reads stdin to record the push, which consumes it. Handing
  # the same bytes back is the whole reason the capture exists.
  write_local_hook 'while read -r lref lsha rref rsha; do
  echo "LOCAL $lref $rref"
done'
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL refs/heads/main refs/heads/main"* ]]
}

@test "a repo-local hook receives every ref of a multi-ref push" {
  write_local_hook 'while read -r lref lsha rref rsha; do
  echo "LOCAL $rref"
done'
  git -C "$TMPDIR/wt" branch -q one
  git -C "$TMPDIR/wt" branch -q two
  run git -C "$TMPDIR/wt" push origin one two
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL refs/heads/one"* ]]
  [[ "$output" == *"LOCAL refs/heads/two"* ]]
}

@test "a repo-local hook is given the remote name and URL" {
  write_local_hook 'echo "LOCAL argv: $1"'
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL argv: origin"* ]]
}

@test "a repo-local hook's output reaches the terminal" {
  # Piping the refs in rather than redirecting them would leave the hook's
  # stdout attached to a pipe nobody reads, and its gates report there.
  write_local_hook 'echo "LOCAL on stdout"; echo "LOCAL on stderr" >&2'
  run git -C "$TMPDIR/wt" push origin main
  [[ "$output" == *"LOCAL on stdout"* ]]
  [[ "$output" == *"LOCAL on stderr"* ]]
}

@test "a repo-local hook that refuses stops the push and records nothing" {
  # A push that never left the machine must not be recorded: reconciliation
  # would find the remote unmoved and report it as a push that vanished.
  write_local_hook 'cat >/dev/null; exit 1'
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -ne 0 ]
  [ ! -f "$INTENTS" ]
}

@test "a refusing repo-local hook leaves an earlier record untouched" {
  git -C "$TMPDIR/wt" push -q origin main
  write_local_hook 'cat >/dev/null; exit 1'
  git -C "$TMPDIR/wt" checkout -q -b refused
  git -C "$TMPDIR/wt" commit -q --allow-empty -m refused
  run git -C "$TMPDIR/wt" push origin refused
  [ "$status" -ne 0 ]
  run branches_recorded
  [ "$output" = "main" ]
}

@test "a repo-local hook that ignores stdin does not block the push" {
  write_local_hook 'exit 0'
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
  run branches_recorded
  [ "$output" = "main" ]
}

@test "a non-executable repo-local hook is skipped, as git itself would" {
  write_local_hook 'exit 1'
  chmod -x "$TMPDIR/wt/.git/hooks/pre-push"
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
  run branches_recorded
  [ "$output" = "main" ]
}

@test "the local hook of a linked worktree is the one the common dir holds" {
  # A linked worktree's .git is a file, and its hooks live in the common dir.
  # Resolving through --git-common-dir is what finds them.
  git -C "$TMPDIR/wt" worktree add -q "$TMPDIR/linked" -b linked
  write_local_hook 'echo "LOCAL from the common dir"; cat >/dev/null'
  run git -C "$TMPDIR/linked" push origin linked
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL from the common dir"* ]]
}

# ── Resolving the workbench checkout ─────────────────────────────────────────

@test "a hook that cannot find its own checkout still pushes and still delegates" {
  # `readlink` returns the link's stored target text and checks nothing, so a
  # relative link is resolved against the repository being pushed rather than
  # against the link's own directory, and lands on a path that is not there.
  # The recorder is unfindable from that, which must cost the record and not
  # the push: this hook runs on every push in every repo on this machine.
  mkdir -p "$TMPDIR/relative/moved/git/hooks"
  cp "$REPO_ROOT/git/hooks/pre-push" "$TMPDIR/relative/moved/git/hooks/pre-push"
  chmod +x "$TMPDIR/relative/moved/git/hooks/pre-push"
  ln -sf "moved/git/hooks/pre-push" "$TMPDIR/relative/pre-push"
  git config --global core.hooksPath "$TMPDIR/relative"
  [ ! -e "$TMPDIR/wt/moved" ]

  write_local_hook 'echo "LOCAL delegated"; cat >/dev/null'
  run git -C "$TMPDIR/wt" push origin main
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL delegated"* ]]
  [ ! -f "$INTENTS" ]
}

# ── Running the hook by hand ─────────────────────────────────────────────────

@test "empty stdin hands the local hook nothing rather than a blank ref" {
  # `printf '%s\n' ""` would emit a lone newline, which a `read -r a b c d` loop
  # takes for a ref with four empty fields.
  write_local_hook 'while read -r lref lsha rref rsha; do
  echo "LOCAL saw a ref: [$lref][$lsha][$rref][$rsha]"
done
echo "LOCAL done"'
  run bash -c "cd '$TMPDIR/wt' && '$TMPDIR/hooks/pre-push' origin '$TMPDIR/remote.git' </dev/null"
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL done"* ]]
  [[ "$output" != *"LOCAL saw a ref"* ]]
  [ ! -f "$INTENTS" ]
}
