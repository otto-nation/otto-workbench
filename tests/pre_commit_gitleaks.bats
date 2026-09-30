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
# failure deterministically. The stub asserts it was passed --exit-code, so a
# hook that stopped sending it cannot quietly pass these.
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

# stub_gitleaks EXIT_CODE [STDERR_TEXT] — a gitleaks that exits EXIT_CODE.
#
# When the hook passes --exit-code N, the stub exits N for the leaks case so
# the hook's own flag decides the verdict; every other requested code is
# returned verbatim. A stub invoked WITHOUT --exit-code writes a marker the
# tests assert on, which is what stops a hook that dropped the flag from
# passing by luck.
stub_gitleaks() {
  local want="$1" msg="${2:-}"
  cat > "$TMPDIR/bin/gitleaks" <<EOF
#!/usr/bin/env bash
saw_exit_code=no
for arg in "\$@"; do
  [[ "\$arg" == "--exit-code" ]] && saw_exit_code=yes
done
if [[ "\$saw_exit_code" == no ]]; then
  echo "STUB-SAW-NO-EXIT-CODE-FLAG"
fi
[[ -n "$msg" ]] && echo "$msg" >&2
exit $want
EOF
  chmod +x "$TMPDIR/bin/gitleaks"
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
}

@test "gitleaks reporting leaks refuses the commit and says secrets" {
  # 7 is the code the hook asks for via --exit-code. The stub returning it is
  # the leaks verdict.
  stub_gitleaks 7
  stage_a_file
  run git -C "$TMPDIR/wt" commit -m "feat: a leaky commit"
  [ "$status" -ne 0 ]
  [[ "$output" == *"found potential secrets"* ]]
  [[ "$output" != *"STUB-SAW-NO-EXIT-CODE-FLAG"* ]]
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
}

@test "a scanner failure names mise as the likely cause" {
  # The diagnostic is the point of the branch: the failure that prompted this
  # cost a session because the message pointed at secrets instead of at PATH.
  stub_gitleaks 1 "mise WARN Error loading settings file"
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
