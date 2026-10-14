#!/usr/bin/env bats
# Tests for step_zsh_shell_tools in zsh/steps.sh: on a machine without
# Homebrew, the zsh component installs what its layers expect Homebrew to
# provide — starship, oh-my-zsh, and the shell Brewfile's zsh plugins — so the
# shell is not left with no prompt, no completion and no plugins.
#
# git and install_package are stubbed: the subject is which installs the
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
# install_package stubbed, on a PATH of the stubs, EXTRA_PATH and the base
# system. No brew unless EXTRA_PATH supplies one.
_run_step() {
  bash -c '
    . "$1/lib/ui.sh"
    . "$1/zsh/steps.sh"
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    install_package() { echo "HELPER $*"; return 0; }
    PATH="$2/stubs${3:+:$3}:/usr/bin:/bin"
    step_zsh_shell_tools
  ' _ "$REPO_ROOT" "$TMPDIR" "${1:-}"
}

# _git_plugins [MANIFEST] — "name url" for each entry the shell Brewfile
# annotates with `git:`, read straight from the file rather than through the
# parser under test.
_git_plugins() {
  sed -n 's/^brew "\([^"]*\)".*# *git: *\([^ ;]*\).*/\1 \2/p' \
    "${1:-$REPO_ROOT/packages/shell/shell.Brewfile}"
}

@test "without Homebrew, starship goes through install_package" {
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER starship"* ]]
}

@test "without Homebrew, oh-my-zsh is cloned to the path the framework snippet reads" {
  run _run_step
  [ "$status" -eq 0 ]
  grep -q -- "clone --quiet --depth 1 https://github.com/ohmyzsh/ohmyzsh.git $HOME/.oh-my-zsh" "$GIT_LOG"
  [ -d "$HOME/.oh-my-zsh" ]
}

@test "every git-annotated entry in the shell Brewfile is cloned from its URL, and nothing else" {
  local plugins name url
  plugins="$(_git_plugins)"
  [ -n "$plugins" ]
  run _run_step
  [ "$status" -eq 0 ]
  while read -r name url; do
    grep -q -- "$url $XDG_DATA_HOME/zsh/plugins/$name" "$GIT_LOG"
  done <<< "$plugins"
  # One clone per plugin plus oh-my-zsh.
  [ "$(wc -l < "$GIT_LOG")" -eq "$(( $(wc -l <<< "$plugins") + 1 ))" ]
}

@test "the clone URL is the annotation, and entries without one are not cloned" {
  export PACKAGES_DIR="$TMPDIR/packages"
  mkdir -p "$PACKAGES_DIR/shell"
  printf '%s\n' \
    'brew "zsh-elsewhere"  # git: https://example.com/other/zsh-elsewhere-repo' \
    'brew "fancy-plugin"  # git: https://example.com/fancy' \
    'brew "zsh-via-mise"  # mise: zsh-via-mise' \
    > "$PACKAGES_DIR/shell/shell.Brewfile"
  run _run_step
  [ "$status" -eq 0 ]
  grep -q -- "https://example.com/other/zsh-elsewhere-repo $XDG_DATA_HOME/zsh/plugins/zsh-elsewhere" "$GIT_LOG"
  grep -q -- "https://example.com/fancy $XDG_DATA_HOME/zsh/plugins/fancy-plugin" "$GIT_LOG"
  [ "$(grep -c zsh-via-mise "$GIT_LOG")" -eq 0 ]
  [ "$(wc -l < "$GIT_LOG")" -eq 3 ]
}

@test "on macOS with Homebrew the step does nothing; the Brewfile owns those installs" {
  mkdir -p "$TMPDIR/brewbin"
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/brewbin/brew"
  printf '#!/bin/sh\necho Darwin\n' > "$TMPDIR/brewbin/uname"
  chmod +x "$TMPDIR/brewbin/brew" "$TMPDIR/brewbin/uname"
  run _run_step "$TMPDIR/brewbin"
  [ "$status" -eq 0 ]
  [[ "$output" != *HELPER* ]]
  [ ! -e "$GIT_LOG" ]
}

@test "on Linux with Homebrew, starship still goes through install_package and plugins are cloned" {
  mkdir -p "$TMPDIR/brewbin"
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/brewbin/brew"
  printf '#!/bin/sh\necho Linux\n' > "$TMPDIR/brewbin/uname"
  chmod +x "$TMPDIR/brewbin/brew" "$TMPDIR/brewbin/uname"
  run _run_step "$TMPDIR/brewbin"
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER starship"* ]]
  grep -q -- "clone --quiet --depth 1 https://github.com/ohmyzsh/ohmyzsh.git $HOME/.oh-my-zsh" "$GIT_LOG"
}

@test "an install already in place is not cloned again" {
  mkdir -p "$HOME/.oh-my-zsh"
  local name
  local url
  while read -r name url; do mkdir -p "$XDG_DATA_HOME/zsh/plugins/$name"; done <<< "$(_git_plugins)"
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
  grep -q -- "zsh-users/zsh-syntax-highlighting" "$GIT_LOG"
}

@test "sync_zsh installs the shell tools before it deploys the layers" {
  # prompt/starship.zsh deploys only once starship is on PATH, so the install
  # has to come first or a fresh machine gets no prompt until a second sync.
  local body
  body="$(sed -n '/^sync_zsh()/,/^}/p' "$REPO_ROOT/zsh/steps.sh")"
  [[ "$body" == *step_zsh_shell_tools*step_zsh$'\n'* ]]
}
