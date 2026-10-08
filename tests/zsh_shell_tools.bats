#!/usr/bin/env bats
# Tests for step_zsh_shell_tools in zsh/steps.sh: on a machine without
# Homebrew, the zsh component installs what its layers expect Homebrew to
# provide — starship, oh-my-zsh, and the shell Brewfile's zsh plugins — so the
# shell is not left with no prompt, no completion and no plugins.
#
# git and install_brew_or_mise are stubbed: the subject is which installs the
# step asks for and how it treats a failure, not the network. HOME and
# XDG_DATA_HOME are the test's own, so nothing reaches the real ~/.oh-my-zsh.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  export XDG_DATA_HOME="$TMPDIR/data"
  mkdir -p "$HOME" "$TMPDIR/stubs"
  export GIT_LOG="$TMPDIR/git-args"

  # A git that records each clone and creates its target, the way a real clone
  # leaves a checkout behind. GIT_FAIL_ON makes a clone of a matching URL fail
  # after creating a partial directory, as an interrupted clone would.
  cat > "$TMPDIR/stubs/git" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$GIT_LOG"
url="${@: -2:1}" dir="${@: -1}"
mkdir -p "$dir"
if [[ -n "${GIT_FAIL_ON:-}" && "$url" == *"$GIT_FAIL_ON"* ]]; then
  exit 128
fi
exit 0
STUB
  chmod +x "$TMPDIR/stubs/git"
}

teardown() {
  common_teardown
}

# _run_step [EXTRA_PATH] — runs step_zsh_shell_tools with the ui helpers and
# install_brew_or_mise stubbed, on a PATH of the stubs, EXTRA_PATH and the base
# system. No brew unless EXTRA_PATH supplies one.
_run_step() {
  bash -c '
    . "$1/lib/ui.sh"
    . "$1/zsh/steps.sh"
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    install_brew_or_mise() { echo "HELPER $*"; return 0; }
    PATH="$2/stubs${3:+:$3}:/usr/bin:/bin"
    step_zsh_shell_tools
  ' _ "$REPO_ROOT" "$TMPDIR" "${1:-}"
}

# _brewfile_plugins — the zsh-* formulae the shell Brewfile lists.
_brewfile_plugins() {
  sed -n 's/^brew "\(zsh-[A-Za-z0-9_-]*\)".*/\1/p' "$REPO_ROOT/brew/shell/shell.Brewfile"
}

@test "without Homebrew, starship goes through the brew-or-mise helper" {
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER starship starship starship starship"* ]]
}

@test "without Homebrew, oh-my-zsh is cloned to the path the framework snippet reads" {
  run _run_step
  [ "$status" -eq 0 ]
  grep -q -- "clone --quiet --depth 1 https://github.com/ohmyzsh/ohmyzsh.git $HOME/.oh-my-zsh" "$GIT_LOG"
  [ -d "$HOME/.oh-my-zsh" ]
}

@test "every zsh plugin the shell Brewfile lists is cloned, and nothing else" {
  local plugins name
  plugins="$(_brewfile_plugins)"
  [ -n "$plugins" ]
  run _run_step
  [ "$status" -eq 0 ]
  for name in $plugins; do
    grep -q -- "https://github.com/zsh-users/$name.git $XDG_DATA_HOME/zsh/plugins/$name" "$GIT_LOG"
  done
  # One clone per plugin plus oh-my-zsh.
  [ "$(wc -l < "$GIT_LOG")" -eq "$(( $(wc -w <<< "$plugins") + 1 ))" ]
}

@test "with Homebrew the step does nothing; the Brewfile owns those installs" {
  mkdir -p "$TMPDIR/brewbin"
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/brewbin/brew"
  chmod +x "$TMPDIR/brewbin/brew"
  run _run_step "$TMPDIR/brewbin"
  [ "$status" -eq 0 ]
  [[ "$output" != *HELPER* ]]
  [ ! -e "$GIT_LOG" ]
}

@test "an install already in place is not cloned again" {
  mkdir -p "$HOME/.oh-my-zsh"
  local name
  for name in $(_brewfile_plugins); do mkdir -p "$XDG_DATA_HOME/zsh/plugins/$name"; done
  run _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$GIT_LOG" ]
}

@test "a failed clone warns, leaves no partial checkout, and the rest still install" {
  GIT_FAIL_ON=ohmyzsh run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN could not clone oh-my-zsh"* ]]
  [ ! -e "$HOME/.oh-my-zsh" ]
  # The plugins after it were still attempted.
  grep -q -- "zsh-users/zsh-syntax-highlighting.git" "$GIT_LOG"
}

@test "sync_zsh installs the shell tools before it deploys the layers" {
  # prompt/starship.zsh deploys only once starship is on PATH, so the install
  # has to come first or a fresh machine gets no prompt until a second sync.
  local body
  body="$(sed -n '/^sync_zsh()/,/^}/p' "$REPO_ROOT/zsh/steps.sh")"
  [[ "$body" == *step_zsh_shell_tools*step_zsh$'\n'* ]]
}
