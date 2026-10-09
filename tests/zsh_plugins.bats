#!/usr/bin/env bats
# Validates zsh/config.d/tools/zsh-plugins.zsh: plugin discovery under
# BREW_PREFIX, and a silent no-op on machines without Homebrew.

setup() {
  load 'test_helper'
  common_setup
  require_zsh
  PLUGINS="$REPO_ROOT/zsh/config.d/tools/zsh-plugins.zsh"
}

teardown() {
  common_teardown
}

@test "no BREW_PREFIX: sources nothing and prints no glob error" {
  run zsh -fc "unset BREW_PREFIX; source '$PLUGINS'; echo done"
  [ "$status" -eq 0 ]
  [ "$output" = "done" ]
}

@test "BREW_PREFIX with no zsh-* plugins: no nomatch error" {
  mkdir -p "$TMPDIR/brew/share"
  run zsh -fc "BREW_PREFIX='$TMPDIR/brew'; source '$PLUGINS'; echo done"
  [ "$status" -eq 0 ]
  [ "$output" = "done" ]
}

@test "BREW_PREFIX plugins are sourced, including through brew's symlinks" {
  mkdir -p "$TMPDIR/brew/Cellar/zsh-foo" "$TMPDIR/brew/share/zsh-foo" "$TMPDIR/brew/share/zsh-bar"
  echo 'echo foo-loaded' > "$TMPDIR/brew/Cellar/zsh-foo/zsh-foo.zsh"
  ln -s "../../Cellar/zsh-foo/zsh-foo.zsh" "$TMPDIR/brew/share/zsh-foo/zsh-foo.zsh"
  echo 'echo bar-loaded' > "$TMPDIR/brew/share/zsh-bar/zsh-bar.zsh"
  run zsh -fc "BREW_PREFIX='$TMPDIR/brew'; source '$PLUGINS'"
  [ "$status" -eq 0 ]
  [[ "$output" == *foo-loaded* ]]
  [[ "$output" == *bar-loaded* ]]
}

# ─── Without Homebrew: the clones zsh/steps.sh makes ──────────────────────────

@test "plugins cloned under the XDG data dir are sourced without Homebrew" {
  mkdir -p "$TMPDIR/data/zsh/plugins/zsh-baz"
  echo 'echo baz-loaded' > "$TMPDIR/data/zsh/plugins/zsh-baz/zsh-baz.zsh"
  run zsh -fc "unset BREW_PREFIX; XDG_DATA_HOME='$TMPDIR/data'; source '$PLUGINS'"
  [ "$status" -eq 0 ]
  [ "$output" = "baz-loaded" ]
}

@test "an empty or missing XDG plugin dir is a silent no-op" {
  run zsh -fc "unset BREW_PREFIX; XDG_DATA_HOME='$TMPDIR/nowhere'; source '$PLUGINS'; echo done"
  [ "$status" -eq 0 ]
  [ "$output" = "done" ]
}

# _const NAME — NAME as lib/constants.sh resolves it under this test's HOME and
# XDG_DATA_HOME.
_const() {
  bash -c 'WORKBENCH_DIR="$1"; . "$1/lib/constants.sh"; printf "%s" "${!2}"' _ "$REPO_ROOT" "$1"
}

@test "the snippet's plugin dir is the one the zsh component clones into" {
  export XDG_DATA_HOME="$TMPDIR/data"
  local dir
  dir="$(_const ZSH_PLUGINS_DIR)"
  [ "$dir" = "$TMPDIR/data/zsh/plugins" ]
  mkdir -p "$dir/zsh-qux"
  echo 'echo qux-loaded' > "$dir/zsh-qux/zsh-qux.zsh"
  run zsh -fc "unset BREW_PREFIX; source '$PLUGINS'"
  [ "$output" = "qux-loaded" ]
}

@test "the framework snippet reads oh-my-zsh from where the zsh component clones it" {
  export HOME="$TMPDIR/home"
  local dir framework="$REPO_ROOT/zsh/config.d/framework/ohmyzsh.zsh"
  dir="$(_const OH_MY_ZSH_DIR)"
  [ "$dir" = "$HOME/.oh-my-zsh" ]
  grep -q '^\[\[ -d "\$HOME/.oh-my-zsh" \]\] || return 0$' "$framework"
  grep -q '^export ZSH="\$HOME/.oh-my-zsh"$' "$framework"
}

@test "a cloned zsh-completions reaches fpath before oh-my-zsh runs compinit" {
  # A stand-in oh-my-zsh that reports fpath at the point compinit would run,
  # under a HOME of the test's own: the real ~/.oh-my-zsh is never touched.
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/.oh-my-zsh" "$TMPDIR/data/zsh/plugins/zsh-completions/src"
  echo 'print -l -- $fpath' > "$HOME/.oh-my-zsh/oh-my-zsh.sh"
  run zsh -fc "XDG_DATA_HOME='$TMPDIR/data'; source '$REPO_ROOT/zsh/config.d/framework/ohmyzsh.zsh'"
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TMPDIR/data/zsh/plugins/zsh-completions/src"* ]]
}
