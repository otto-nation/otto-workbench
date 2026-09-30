#!/usr/bin/env bats
# Tests for the global pre-commit hook's secret scan, specifically that it
# distinguishes "gitleaks found secrets" from "gitleaks could not run".
#
# gitleaks exits 1 for both outcomes, so the hook passes --exit-code to move
# the leaks verdict onto a code nothing else uses. The bug these cover: with a
# bare `|| echo "found potential secrets"`, a gitleaks that cannot start — a
# mise shim refusing to exec because some .mise.toml does not parse — is
# reported as a secret finding, and the commit is refused for a leak that does
# not exist. The scanner never ran.
#
# gitleaks is stubbed rather than real: the case under test is how the hook
# reads an exit code, and a stub is the only way to produce an operational
# failure deterministically.
#
# The stub honours --exit-code the way the real binary does: asked to simulate
# leaks it exits with whatever VALUE the hook passed, falling back to gitleaks'
# default of 1 when the hook passed no flag at all. That is what makes these
# tests sensitive to the fix rather than to the stub. A hook that dropped the
# flag, or passed `--exit-code 1` and reinstated the collision, gets 1 back and
# is caught by the leaks case instead of quietly still passing.
#
# A lab of its own each time: GIT_CONFIG_GLOBAL points at a temp gitconfig
# whose core.hooksPath holds a symlink to this checkout's hook, exactly as
# step_global_hooks installs it. Nothing here reaches the developer's own hook
# path or repositories.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  mkdir -p "$TMPDIR/hooks" "$TMPDIR/bin"
  ln -sf "$REPO_ROOT/git/hooks/pre-commit" "$TMPDIR/hooks/pre-commit"

  export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
  git config --global core.hooksPath "$TMPDIR/hooks"
  # Not a placeholder identity: the hook refuses *@example.com and friends
  # before it ever reaches the secret scan, which would pass these tests for
  # the wrong reason.
  git config --global user.name "Ada Lovelace"
  git config --global user.email "ada@analytical.dev"
  git config --global init.defaultBranch main
  git config --global commit.gpgsign false

  git init -q -b main "$TMPDIR/wt"
  # A forge remote, so the hook's identity check runs the same path it does on
  # a real repository rather than the throwaway-repo exemption.
  git -C "$TMPDIR/wt" remote add origin "https://github.com/example/example.git"

  # The stub shadows any real or shimmed gitleaks for the hook's `command -v`.
  export PATH="$TMPDIR/bin:$PATH"
}

teardown() {
  common_teardown
}

# stub_gitleaks MODE [STDERR_TEXT] — a fake gitleaks.
#
# MODE is either "leaks", or a literal exit code for the operational cases.
#
# "leaks" reproduces the real binary's contract: exit with the VALUE of
# --exit-code when the caller passed one, else gitleaks' default of 1. So the
# verdict the hook reads is decided by the flag the hook actually sent, not by
# the stub — which is what lets the leaks case catch a dropped flag, or a flag
# set back to 1.
#
# It also records its argv and the fact that it ran, so a test can tell
# "scanner said clean" apart from "scanner never executed".
stub_gitleaks() {
  local mode="$1" msg="${2:-}"
  cat > "$TMPDIR/bin/gitleaks" <<EOF
#!/usr/bin/env bash
touch "$TMPDIR/gitleaks-ran"
printf '%s\n' "\$@" > "$TMPDIR/gitleaks-argv"

# The real binary's default leaks code; --exit-code overrides it.
leak_code=1
prev=""
for arg in "\$@"; do
  [[ "\$prev" == "--exit-code" ]] && leak_code="\$arg"
  prev="\$arg"
done

[[ -n "$msg" ]] && echo "$msg" >&2

if [[ "$mode" == leaks ]]; then
  echo "secret detected in file.txt"
  exit "\$leak_code"
fi
exit $mode
EOF
  chmod +x "$TMPDIR/bin/gitleaks"
}

# assert_scanner_ran — the hook actually executed gitleaks.
#
# Without this a hook that skipped the scan entirely passes the clean case.
assert_scanner_ran() {
  [[ -f "$TMPDIR/gitleaks-ran" ]]
}

# assert_exit_code_flag — the hook passed --exit-code with the value the
# three-way contract depends on. Asserted in the operational cases too, so the
# flag is held by more than the single leaks test.
assert_exit_code_flag() {
  # The expected value is read out of the hook rather than written here, so
  # the constant lives in one place. Hardcoding it would leave a second copy
  # that a change to the hook has to remember to update by hand, and the test
  # would fail on the literal rather than on the behaviour.
  local expected
  expected=$(sed -n 's/^_GITLEAKS_LEAK_EXIT=\([0-9]*\)$/\1/p' \
    "$REPO_ROOT/git/hooks/pre-commit")
  [[ -n "$expected" ]]
  grep -qx -- "--exit-code" "$TMPDIR/gitleaks-argv"
  grep -qx -- "$expected" "$TMPDIR/gitleaks-argv"
}

# stage_a_file — one staged change, so there is something to commit.
stage_a_file() {
  echo "content" > "$TMPDIR/wt/file.txt"
  git -C "$TMPDIR/wt" add file.txt
}

@test "a clean scan lets the commit through" {
  stub_gitleaks 0
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: a clean commit"
  [ "$status" -eq 0 ]
  # A hook that skipped the scan entirely would also exit 0 here.
  assert_scanner_ran
  assert_exit_code_flag
}

@test "gitleaks reporting leaks refuses the commit and says secrets" {
  # The stub exits with whatever --exit-code the hook passed, so this fails if
  # the hook drops the flag (stub falls back to 1, which the hook then reads as
  # a scanner failure) or sets it back to 1 (the collision this branch removes).
  stub_gitleaks leaks
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: a leaky commit"
  [ "$status" -ne 0 ]
  [[ "$output" == *"found potential secrets"* ]]
  [[ "$output" != *"could not run"* ]]
  assert_exit_code_flag
}

@test "the findings gitleaks printed reach the user on a leak" {
  # Blocking without saying what matched leaves nothing to act on.
  stub_gitleaks leaks
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: a leaky commit"
  [ "$status" -ne 0 ]
  [[ "$output" == *"secret detected in file.txt"* ]]
}

@test "a gitleaks that cannot run is not reported as a secret finding" {
  # The regression. Exit 1 is what a mise shim returns when it refuses to exec,
  # and it is also gitleaks' DEFAULT leaks code — which is exactly why the hook
  # must move the leaks verdict off 1. Before the fix this printed
  # "found potential secrets" for a scan that never happened.
  stub_gitleaks 1 "mise WARN Error loading settings file .mise.toml: TOML parse error"
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: a commit while the shim is broken"

  [ "$status" -ne 0 ]                                  # still refused: fail closed
  [[ "$output" != *"found potential secrets"* ]]       # but NOT as a leak
  [[ "$output" == *"could not run"* ]]
  [[ "$output" == *"NOT a secret finding"* ]]
  assert_exit_code_flag
}

@test "a scanner failure points at PATH rather than at the diff" {
  # The diagnostic is the point of the branch: the failure that prompted this
  # cost a session because the message pointed at secrets instead of at PATH.
  #
  # The scanner's own message deliberately does NOT mention mise here, so only
  # the hook's own guidance can satisfy these assertions — a stub that said
  # "mise ..." would match a *mise* assertion by itself and prove nothing.
  stub_gitleaks 1 "fatal: boom"
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: broken shim"
  [ "$status" -ne 0 ]
  [[ "$output" == *"mise"* ]]
  [[ "$output" == *"command -v gitleaks"* ]]
}

# passes-at-base: the old hook let gitleaks write straight to the terminal, so
# its stderr reached the user there too. This holds that property through the
# rewrite, where the output is captured and must be deliberately re-emitted.
@test "the scanner's own output survives into a failure report" {
  # Whatever gitleaks said about why it could not start is the thing that
  # diagnoses it, so the hook must not swallow it.
  stub_gitleaks 1 "TOML parse error at line 1, column 6"
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: broken shim"
  [ "$status" -ne 0 ]
  [[ "$output" == *"TOML parse error"* ]]
}

@test "an unexpected exit code is treated as a scanner failure, not a leak" {
  # 126/127 are PATH and permission failures — a shim that is not executable.
  # Anything that is neither 0 nor the leaks code means the scan did not
  # produce a verdict.
  stub_gitleaks 127
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: gitleaks not executable"
  [ "$status" -ne 0 ]
  [[ "$output" != *"found potential secrets"* ]]
  [[ "$output" == *"could not run"* ]]
}
