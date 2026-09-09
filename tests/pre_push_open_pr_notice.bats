#!/usr/bin/env bats
# Tests for the pre-push notice that a branch already has an open PR.
#
# The function is sourced out of the workbench hook and run against a stub `gh`,
# rather than by pushing to a real remote: what is under test is how it reads
# `gh pr view`'s answer, and a real forge would make that answer the one thing
# the test cannot vary.
#
# The notice must never refuse a push. Pushing to a branch under review is
# routine — review findings, CI fixes — and a gate here would be answered with
# --no-verify instead of thought.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  SANDBOX="$(mktemp -d)"
  export TMPDIR="$SANDBOX"

  HOOK="$REPO_ROOT/git/hooks/pre-push-workbench"

  # The function reads REPO_ROOT to find the branch, so it points at the
  # fixture repo for the duration — not at this checkout.
  FIXTURE="$TMPDIR/repo"
  mkdir -p "$FIXTURE"
  git -C "$FIXTURE" init -q -b feat/thing .
  git -C "$FIXTURE" commit -q --allow-empty -m "x"
  export REPO_ROOT="$FIXTURE"
  export GIT_REMOTE=origin

  STUB_BIN="$TMPDIR/stub"
  mkdir -p "$STUB_BIN"
  PATH="$STUB_BIN:$PATH"

  warn() { printf 'WARN: %s\n' "$*"; }
  export -f warn
  eval "$(sed -n '/^_run_briefly()/,/^}/p' "$HOOK")"
  eval "$(sed -n '/^_warn_if_pr_is_open()/,/^}/p' "$HOOK")"
}

teardown() {
  rm -rf "$SANDBOX"
  common_teardown
}

# stub_gh ISDRAFT — a `gh` answering `pr list --jq` with one TSV line.
#
# The real query passes --state open, so the server returns nothing for a merged
# or closed PR. stub_gh_empty is that case; there is no state field to fake.
stub_gh() {
  cat > "$STUB_BIN/gh" <<EOF
#!/usr/bin/env bash
printf '%s\t%s\n' "$1" "https://example.test/pull/1"
EOF
  chmod +x "$STUB_BIN/gh"
}

# stub_gh_empty — a `gh` finding no open PR for the branch, which is what
# --state open returns once one is merged or closed.
stub_gh_empty() {
  printf '#!/usr/bin/env bash\nprintf \x27\\n\x27\n' > "$STUB_BIN/gh"
  chmod +x "$STUB_BIN/gh"
}

@test "an open PR marked ready says so" {
  stub_gh false
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [[ "$output" == *"open PR"* ]]
  [[ "$output" == *"ready for review"* ]]
}

@test "an open draft is reported without the ready line" {
  stub_gh true
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [[ "$output" == *"open PR"* ]]
  [[ "$output" != *"ready for review"* ]]
}

@test "a merged PR is silent" {
  stub_gh_empty
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a closed PR is silent" {
  stub_gh_empty
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a branch with no PR is silent" {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/gh"
  chmod +x "$STUB_BIN/gh"
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "no gh installed leaves the push alone" {
  # A machine without the CLI must push exactly as it did before.
  run env PATH="/usr/bin:/bin" bash -c "
    warn() { printf 'WARN: %s\n' \"\$*\"; }
    REPO_ROOT='$REPO_ROOT'; GIT_REMOTE=origin
    $(sed -n '/^_run_briefly()/,/^}/p' "$HOOK")
    $(sed -n '/^_warn_if_pr_is_open()/,/^}/p' "$HOOK")
    _warn_if_pr_is_open"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a detached HEAD is silent" {
  # No branch to ask about, and `gh pr view ''` would answer for whatever the
  # default branch is.
  git -C "$REPO_ROOT" checkout -q --detach
  stub_gh false
  run _warn_if_pr_is_open
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a slow gh still lets the push finish" {
  # The notice is best-effort, so a forge that answers slowly must not be the
  # reason a push stalls. Asserted on elapsed time rather than an exit code:
  # what matters is that the function returned, not how.
  cat > "$STUB_BIN/gh" <<'EOF'
#!/usr/bin/env bash
sleep 2
printf 'OPEN\tfalse\thttps://example.test/pull/1\n'
EOF
  chmod +x "$STUB_BIN/gh"

  local before after
  before=$SECONDS
  run _warn_if_pr_is_open
  after=$SECONDS
  [ "$status" -eq 0 ]
  # It waits for the answer it asked for, and no longer.
  [ "$((after - before))" -lt 10 ]
}

@test "a hung gh is abandoned rather than waited on" {
  # The case the deadline exists for: not a slow answer, but no answer. A dead
  # TLS handshake is not an error gh returns from — it is one it sits on.
  cat > "$STUB_BIN/gh" <<'EOF'
#!/usr/bin/env bash
sleep 300
EOF
  chmod +x "$STUB_BIN/gh"

  local before after
  before=$SECONDS
  run _warn_if_pr_is_open
  after=$SECONDS
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  # Bounded at 5s by _run_briefly; the slack is for a loaded machine.
  [ "$((after - before))" -lt 20 ]
}

@test "the deadline kills the process it gave up on" {
  # A stub that outlives the deadline must not be left running behind the push.
  cat > "$STUB_BIN/gh" <<EOF
#!/usr/bin/env bash
sleep 300 & echo \$! > "$TMPDIR/child.pid"
wait
EOF
  chmod +x "$STUB_BIN/gh"

  run _run_briefly 2 gh pr list
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}
