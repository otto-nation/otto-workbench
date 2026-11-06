#!/usr/bin/env bats
# Global commit-msg and pre-push hooks check cross-group references.
# Sources named as $REPO_ROOT literals so bin/local/select-tests maps this
# suite to $REPO_ROOT/git/hooks/commit-msg, $REPO_ROOT/git/hooks/pre-push,
# and $REPO_ROOT/lib/owners_check.sh.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  sandbox_state_dir
  INTENTS="$WORKBENCH_STATE_DIR/push-intents.json"

  mkdir -p "$TMPDIR/hooks"
  ln -sf "$REPO_ROOT/git/hooks/commit-msg" "$TMPDIR/hooks/commit-msg"
  ln -sf "$REPO_ROOT/git/hooks/pre-push" "$TMPDIR/hooks/pre-push"

  PATH="$REPO_ROOT/bin:$PATH"
  unset "${!WORKBENCH_ALLOW_@}"

  git config --global core.hooksPath "$TMPDIR/hooks"
  git config --global user.name t
  git config --global user.email t@t
  git config --global init.defaultBranch main
  git config --global commit.gpgsign false

  W="$TMPDIR/wt"
  git init -q -b main "$W"
  git -C "$W" remote add origin "https://github.com/otto-nation/svc.git"
}

teardown() {
  common_teardown
}

# _two_groups — github.owners with otto-nation in work and me in personal.
_two_groups() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' \
    'github:' \
    '  owners:' \
    '    work: [otto-nation]' \
    '    personal: [me]' \
    > "$WORKBENCH_CONFIG_DIR/config.yml"
}

# _msg FILE BODY — write BODY to FILE.
_msg() {
  printf '%s\n' "$2" > "$1"
}

@test "OWNERS_CHECK_REFUSED matches cli.owners_check.REFUSED" {
  local sh py
  sh="$(bash -c '. "$1"; printf %s "$OWNERS_CHECK_REFUSED"' \
    _ "$REPO_ROOT/lib/owners_check.sh")"
  py="$(python3 -I -c \
    'import sys; sys.path.insert(0, sys.argv[1]); from cli.owners_check import REFUSED; print(REFUSED)' \
    "$REPO_ROOT/ai/lib")"
  [ "$sh" = "$py" ]
  [ "$sh" = 2 ]
}

@test "commit-msg refuses a cross-group message by path and through git commit" {
  _two_groups
  _msg "$TMPDIR/msg" "see me/lib#12"
  cd "$W" || return 1

  run "$REPO_ROOT/git/hooks/commit-msg" "$TMPDIR/msg"
  [ "$status" -eq 1 ]
  [[ "$output" == *"me/lib#12"* ]]

  run git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 1 ]
  [[ "$output" == *"me/lib#12"* ]]
}

@test "commit-msg honours gh.owners.ALLOW_CROSS_REF_ENV" {
  # Spell the override named by ai/lib/gh/owners.py ALLOW_CROSS_REF_ENV.
  _two_groups
  cd "$W" || return 1
  run env WORKBENCH_ALLOW_CROSS_REF=1 git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 0 ]
  [[ "$output" == *"WORKBENCH_ALLOW_CROSS_REF=1"* ]]
}

@test "commit-msg allows an own-group message" {
  _two_groups
  cd "$W" || return 1
  run git commit --allow-empty -m "see otto-nation/svc#12"
  [ "$status" -eq 0 ]
}

@test "a hook whose workbench checkout moved still allows" {
  _two_groups
  mkdir -p "$TMPDIR/relative/moved/git/hooks"
  cp "$REPO_ROOT/git/hooks/commit-msg" "$TMPDIR/relative/moved/git/hooks/commit-msg"
  chmod +x "$TMPDIR/relative/moved/git/hooks/commit-msg"
  ln -sf "moved/git/hooks/commit-msg" "$TMPDIR/relative/commit-msg"
  git config --global core.hooksPath "$TMPDIR/relative"
  [ ! -e "$W/moved" ]

  cd "$W" || return 1
  run git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 0 ]
  [[ "$output" != *"could not run"* ]]
}

@test "a hook whose owners_check.py is missing still allows" {
  _two_groups
  local wb="$TMPDIR/wb"
  mkdir -p "$wb/git/hooks" "$wb/lib"
  cp "$REPO_ROOT/git/hooks/commit-msg" "$wb/git/hooks/commit-msg"
  cp "$REPO_ROOT/lib/owners_check.sh" "$wb/lib/owners_check.sh"
  chmod +x "$wb/git/hooks/commit-msg"
  ln -sf "$wb/git/hooks/commit-msg" "$TMPDIR/hooks/commit-msg"

  cd "$W" || return 1
  run git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 0 ]
  [[ "$output" != *"could not run"* ]]
}

@test "repo-local commit-msg still runs after the owner check" {
  _two_groups
  mkdir -p "$W/.git/hooks"
  printf '#!/usr/bin/env bash\necho LOCAL commit-msg\n' > "$W/.git/hooks/commit-msg"
  chmod +x "$W/.git/hooks/commit-msg"

  cd "$W" || return 1
  run git commit --allow-empty -m "see otto-nation/svc#12"
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL commit-msg"* ]]
}

@test "a refusing repo-local commit-msg still fails the commit" {
  _two_groups
  mkdir -p "$W/.git/hooks"
  printf '#!/usr/bin/env bash\necho LOCAL refuse\nexit 1\n' > "$W/.git/hooks/commit-msg"
  chmod +x "$W/.git/hooks/commit-msg"

  cd "$W" || return 1
  run git commit --allow-empty -m "see otto-nation/svc#12"
  [ "$status" -eq 1 ]
  [[ "$output" == *"LOCAL refuse"* ]]
}

@test "a broken owners config allows with a warning" {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' \
    'github:' \
    '  owners:' \
    '    work: [acme]' \
    '    personal: [acme]' \
    > "$WORKBENCH_CONFIG_DIR/config.yml"

  cd "$W" || return 1
  run git commit --allow-empty -m "see acme/lib#1"
  [ "$status" -eq 0 ]
  [[ "$output" == *"owner check could not run"* ]]
}

@test "unconfigured github.owners allows" {
  cd "$W" || return 1
  run git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 0 ]
  [[ "$output" != *"could not run"* ]]
}

@test "pre-push refuses an outgoing cross-group commit made with -n" {
  _two_groups
  cd "$W" || return 1
  git commit -n -q --allow-empty -m "see me/lib#12"
  local sha zero
  sha="$(git rev-parse HEAD)"
  zero="0000000000000000000000000000000000000000"
  run bash -c 'printf "%s\n" "$1" | "$2" origin "$3"' \
    _ "refs/heads/main $sha refs/heads/main $zero" \
    "$REPO_ROOT/git/hooks/pre-push" \
    "https://github.com/otto-nation/svc.git"
  [ "$status" -eq 1 ]
  [[ "$output" == *"me/lib#12"* ]]
}

@test "a non-refused push still replays refs and records intent" {
  git init -q --bare -b main "$TMPDIR/remote.git"
  git -C "$W" remote set-url origin "$TMPDIR/remote.git"
  mkdir -p "$W/.git/hooks"
  printf '%s\n' '#!/usr/bin/env bash' \
    'while read -r lref lsha rref rsha; do echo "LOCAL $lref $rref"; done' \
    > "$W/.git/hooks/pre-push"
  chmod +x "$W/.git/hooks/pre-push"

  cd "$W" || return 1
  git commit -n -q --allow-empty -m init
  run git push origin main
  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL refs/heads/main refs/heads/main"* ]]
  [ -f "$INTENTS" ]
}

@test "a failing python is reported and waved through" {
  _two_groups
  mkdir -p "$TMPDIR/bin"
  printf '#!/bin/sh\nexit 1\n' > "$TMPDIR/bin/python3"
  chmod +x "$TMPDIR/bin/python3"

  cd "$W" || return 1
  PATH="$TMPDIR/bin:$PATH" run git commit --allow-empty -m "see me/lib#12"
  [ "$status" -eq 0 ]
  [[ "$output" == *"owner check could not run (exit 1)"* ]]
}
