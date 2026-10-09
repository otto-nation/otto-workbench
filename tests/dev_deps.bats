#!/usr/bin/env bats
# Tests for bin/local/dev-deps: the one declaration of what developing
# otto-workbench needs, installed without root where possible and checked —
# never sudo'd — where not.
#
# The script is sourced and its helpers stubbed afterwards: it loads lib/ui.sh,
# which would replace any stub defined before it. install_brew_or_mise is
# stubbed to record its arguments, so no case installs anything.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"
  export MISE_LOG="$TMPDIR/mise-args"
  cat > "$STUBS/mise" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$MISE_LOG"
EOF
  chmod +x "$STUBS/mise"
}

teardown() {
  common_teardown
}

# _run FN [PATH] — runs FN from dev-deps with the ui helpers and the installer
# stubbed, on PATH (default: the base system only, so no brew and no mise).
_run() {
  bash -c '
    . "$1/bin/local/dev-deps"
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    err()     { echo "ERR $*"; }
    install_brew_or_mise() { echo "HELPER $*"; return 0; }
    _cmd_runnable() { command -v "$1" >/dev/null 2>&1; }
    PATH="$3"
    "$2"
  ' _ "$REPO_ROOT" "$1" "${2:-/usr/bin:/bin}"
}

# _sc_pin — the CI action's shellcheck pin, read the way the script reads it.
_sc_pin() {
  sed -n 's/^[[:space:]]*SHELLCHECK_VERSION:[[:space:]]*v//p' \
    "$REPO_ROOT/.github/actions/install-shellcheck/action.yml"
}

# _sc_lab VERSION [WITH_MISE] — a PATH with a shellcheck reporting VERSION, and
# mise unless WITH_MISE is "no". Never brew.
_sc_lab() {
  local dir="$TMPDIR/sc-lab-$1-${2:-yes}"
  mkdir -p "$dir"
  [[ "${2:-yes}" == no ]] || ln -sf "$STUBS/mise" "$dir/mise"
  printf '#!/bin/sh\necho "ShellCheck"\necho "version: %s"\n' "$1" > "$dir/shellcheck"
  chmod +x "$dir/shellcheck"
  echo "$dir:/usr/bin:/bin"
}

@test "loads its libraries under an exported GIT_DIR, as the pre-push hook runs it" {
  # Any GIT_DIR makes git skip discovery, so a root found with `git rev-parse
  # --show-toplevel` answers the cwd instead. A scratch repo stands in for the
  # hook's, so nothing here can touch the real one.
  git init -q "$TMPDIR/hook-repo"
  run env GIT_DIR="$TMPDIR/hook-repo/.git" bash -c '
    cd "$2" && . "$1/bin/local/dev-deps" && declare -F dev_deps_install
  ' _ "$REPO_ROOT" "$TMPDIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dev_deps_install"* ]]
}

@test "install covers every declared installable dependency, shellcheck and pytest" {
  run _run dev_deps_install
  [ "$status" -eq 0 ]
  local cmd
  for cmd in bats jq yq node; do
    [[ "$output" == *"HELPER $cmd "* ]]
  done
  [[ "$output" == *"HELPER shellcheck shellcheck shellcheck@$(_sc_pin) shellcheck"* ]]
  [[ "$output" == *"HELPER pytest pytest pipx:pytest[uvx_args=--with pytest-xdist] pytest"* ]]
}

@test "without Homebrew, uv is installed before pytest" {
  run _run _install_pytest
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER uv uv uv uv"*"HELPER pytest "* ]]
}

@test "with Homebrew, uv is left alone" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/brew"
  run _run _install_pytest "$STUBS:/usr/bin:/bin"
  [ "$status" -eq 0 ]
  [[ "$output" != *"HELPER uv "* ]]
}

@test "a shellcheck at another version is moved to the CI pin on the mise path" {
  [ -n "$(_sc_pin)" ]
  run _run _install_pinned_shellcheck "$(_sc_lab 0.0.1)"
  [ "$status" -eq 0 ]
  [ "$(cat "$MISE_LOG")" = "use -g shellcheck@$(_sc_pin)" ]
}

@test "a distro shellcheck with no mise is left to the helper rather than run through mise" {
  run _run _install_pinned_shellcheck "$(_sc_lab 0.0.1 no)"
  [[ "$output" != *"mise: command not found"* ]]
  [[ "$output" == *"HELPER shellcheck shellcheck shellcheck@$(_sc_pin) shellcheck"* ]]
}

@test "a shellcheck already at the CI pin is left alone" {
  run _run _install_pinned_shellcheck "$(_sc_lab "$(_sc_pin)")"
  [ "$status" -eq 0 ]
  [ ! -e "$MISE_LOG" ]
}

@test "check names each missing dependency with its fix and fails" {
  # A PATH with none of the dependencies: only a locale stub, reporting none.
  printf '#!/bin/sh\necho C\necho POSIX\n' > "$STUBS/locale"
  chmod +x "$STUBS/locale"
  for tool in sed tr uname cat; do ln -sf "$(command -v "$tool")" "$STUBS/$tool"; done
  run _run dev_deps_check "$STUBS"
  [ "$status" -eq 1 ]
  [[ "$output" == *"bats-core — run: bin/local/dev-deps"* ]]
  [[ "$output" == *"GNU parallel (parallel bats runs) — run: sudo apt install parallel"* ]]
  if [[ "$(uname -s)" != Darwin ]]; then
    [[ "$output" == *"locale en_US.UTF-8 — run: sudo locale-gen en_US.UTF-8"* ]]
  fi
}

@test "on Darwin the root deps hint brew install, not sudo apt install" {
  mkdir -p "$STUBS/darwin-uname"
  printf '#!/bin/sh\necho Darwin\n' > "$STUBS/darwin-uname/uname"
  chmod +x "$STUBS/darwin-uname/uname"
  run bash -c 'PATH="$1:$PATH"; . "$2/bin/local/dev-deps"; printf "%s\n" "${DEV_DEPS_ROOT[@]}"' \
    _ "$STUBS/darwin-uname" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"zsh|zsh|brew install zsh"* ]]
  [[ "$output" == *"GNU parallel (parallel bats runs)|brew install parallel"* ]]
  [[ "$output" != *"sudo apt install"* ]]
}

@test "the glibc utf8 spelling of the locale counts as present" {
  printf '#!/bin/sh\necho C.utf8\necho en_US.utf8\n' > "$STUBS/locale"
  chmod +x "$STUBS/locale"
  run bash -c '. "$1/bin/local/dev-deps"; PATH="$2:/usr/bin:/bin"; _has_locale en_US.UTF-8' _ "$REPO_ROOT" "$STUBS"
  [ "$status" -eq 0 ]
}

@test "a locale list without it reads as missing" {
  printf '#!/bin/sh\necho C\necho C.utf8\necho POSIX\n' > "$STUBS/locale"
  chmod +x "$STUBS/locale"
  run bash -c '. "$1/bin/local/dev-deps"; PATH="$2:/usr/bin:/bin"; _has_locale en_US.UTF-8' _ "$REPO_ROOT" "$STUBS"
  [ "$status" -eq 1 ]
}
