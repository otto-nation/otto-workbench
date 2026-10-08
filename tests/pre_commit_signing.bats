#!/usr/bin/env bats
# Tests for the global pre-commit hook's signing check: a commit whose OpenPGP
# signature needs a passphrase prompt that nothing can show is refused at once,
# instead of `git commit` hanging on pinentry with no output.
#
# gpg is stubbed through gpg.program: the case under test is how the hook reads
# a non-interactive signing probe, and a stub is the only way to produce a
# locked key deterministically. Whether a prompt could be shown is chosen with
# the hook's WORKBENCH_UNAME and WORKBENCH_TTY_DEVICE seams, so the answer does
# not depend on whether bats itself runs in a terminal.
#
# Passing cases run the hook directly: through `git commit`, git would go on to
# sign with the stub. Refusals run through `git commit`, which never reaches
# signing because the hook stops it first.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  mkdir -p "$TMPDIR/hooks" "$TMPDIR/bin"
  ln -sf "$REPO_ROOT/git/hooks/pre-commit" "$TMPDIR/hooks/pre-commit"

  export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
  git config --global core.hooksPath "$TMPDIR/hooks"
  git config --global user.name "Ada Lovelace"
  git config --global user.email "ada@analytical.dev"
  git config --global init.defaultBranch main
  git config --global commit.gpgsign true
  git config --global user.signingkey ABCD1234
  git config --global gpg.program "$TMPDIR/bin/gpg"

  git init -q -b main "$TMPDIR/wt"
  echo content > "$TMPDIR/wt/file.txt"
  git -C "$TMPDIR/wt" add file.txt

  # A clean scan, so a refusal can only come from the signing check.
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/bin/gitleaks"
  chmod +x "$TMPDIR/bin/gitleaks"
  export PATH="$TMPDIR/bin:$PATH"

  # No prompt possible unless a test says otherwise.
  unset DISPLAY WAYLAND_DISPLAY
  export WORKBENCH_UNAME=Linux
  export WORKBENCH_TTY_DEVICE="$TMPDIR/no-such-tty"
}

teardown() {
  common_teardown
}

# stub_gpg MODE — a fake gpg recording its argv. "locked" fails the way gpg
# does with --pinentry-mode error and an uncached passphrase; "expired" fails
# the way it does for an expired key, needing no prompt at all; "ok" signs.
stub_gpg() {
  local mode="$1"
  cat > "$TMPDIR/bin/gpg" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$TMPDIR/gpg-argv"
printf '%s' "\${LC_ALL:-}" > "$TMPDIR/gpg-lc"
cat >/dev/null
if [[ "$mode" == locked ]]; then
  echo "gpg: signing failed: No pinentry" >&2
  exit 2
fi
if [[ "$mode" == expired ]]; then
  echo 'gpg: skipped "ABCD1234": Unusable secret key' >&2
  exit 2
fi
echo "-----BEGIN PGP SIGNED MESSAGE-----"
STUB
  chmod +x "$TMPDIR/bin/gpg"
}

_hook() {
  (cd "$TMPDIR/wt" && "$REPO_ROOT/git/hooks/pre-commit")
}

@test "a locked key with no way to prompt refuses the commit instead of hanging" {
  stub_gpg locked
  run git -C "$TMPDIR/wt" commit -q -m msg
  [ "$status" -ne 0 ]
  [[ "$output" == *"commit signing cannot finish here"* ]]
  [[ "$output" == *"signing failed: No pinentry"* ]]
  [[ "$output" == *"--clearsign -u ABCD1234"* ]]
  [[ "$output" == *"git -c commit.gpgsign=false commit"* ]]
  run git -C "$TMPDIR/wt" rev-parse -q --verify HEAD
  [ "$status" -ne 0 ]
}

@test "the probe never prompts and signs with the configured key" {
  stub_gpg ok
  run _hook
  [ "$status" -eq 0 ]
  grep -qx -- "--pinentry-mode" "$TMPDIR/gpg-argv"
  grep -qx -- "error" "$TMPDIR/gpg-argv"
  grep -qx -- "--batch" "$TMPDIR/gpg-argv"
  grep -qx -- "ABCD1234" "$TMPDIR/gpg-argv"
}

@test "a locked key passes when a terminal can show the prompt" {
  stub_gpg locked
  WORKBENCH_TTY_DEVICE=/dev/null run _hook
  [ "$status" -eq 0 ]
}

@test "a locked key passes on macOS, where pinentry-mac prompts without a terminal" {
  stub_gpg locked
  WORKBENCH_UNAME=Darwin run _hook
  [ "$status" -eq 0 ]
}

@test "a locked key passes under a graphical display" {
  stub_gpg locked
  DISPLAY=:0 run _hook
  [ "$status" -eq 0 ]
}

@test "signing off skips the probe entirely" {
  stub_gpg locked
  git config --global commit.gpgsign false
  run _hook
  [ "$status" -eq 0 ]
  [ ! -e "$TMPDIR/gpg-argv" ]
}

@test "ssh signing is not probed with gpg" {
  stub_gpg locked
  git config --global gpg.format ssh
  run _hook
  [ "$status" -eq 0 ]
  [ ! -e "$TMPDIR/gpg-argv" ]
}

@test "the escape the refusal names commits unsigned" {
  stub_gpg locked
  run git -C "$TMPDIR/wt" -c commit.gpgsign=false commit -q -m msg
  [ "$status" -eq 0 ]
  git -C "$TMPDIR/wt" rev-parse -q --verify HEAD
}

@test "a key that fails for another reason is left to git, not blamed on a prompt" {
  stub_gpg expired
  run _hook
  [ "$status" -eq 0 ]
  [[ "$output" != *"passphrase prompt"* ]]
  [ -e "$TMPDIR/gpg-argv" ]
}

@test "the probe runs gpg with LC_ALL=C so its wording is matchable" {
  stub_gpg ok
  LC_ALL=de_DE.UTF-8 run _hook
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/gpg-lc")" = C ]
}
