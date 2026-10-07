#!/usr/bin/env bats
# Validates zsh/config.d/tools/zsh-plugins.zsh: plugin discovery under
# BREW_PREFIX, and a silent no-op on machines without Homebrew.

setup() {
  load 'test_helper'
  common_setup
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
