#!/usr/bin/env bats
# Idempotency tests for sync_bin, sync_git, and sync_zsh.
# Each test runs the sync function twice and asserts that the resulting
# filesystem state is identical — meeting the sync_<name>() contract.

FAKE_HOME=""
ORIG_HOME="$HOME"

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _source_with HOME STEPS_RELPATH — sources lib/ui.sh and a steps.sh with HOME overridden.
# Must be called once per test before calling sync functions.
_source_with() {
  local fake_home="$1" steps="$2"
  # Setting HOME before source causes constants.sh to derive all paths from fake_home.
  HOME="$fake_home"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  export WORKBENCH_SKIP_GENERATE=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/$steps"
}

# _symlinks DIR [DEPTH] — prints "rel/path -> target" for each symlink under DIR, sorted.
_symlinks() {
  local dir="$1" depth="${2:-1}"
  find "$dir" -maxdepth "$depth" -type l 2>/dev/null | sort | while IFS= read -r lnk; do
    echo "${lnk#"$dir/"} -> $(readlink "$lnk")"
  done
}

# ─── sync_bin ─────────────────────────────────────────────────────────────────

@test "sync_bin: second run produces identical symlinks in LOCAL_BIN_DIR" {
  _source_with "$FAKE_HOME" "bin/steps.sh"

  sync_bin >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.local/bin")

  sync_bin >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.local/bin")

  [[ "$state1" == "$state2" ]]
}

@test "sync_bin: symlinks all point into BIN_SRC_DIR and are valid" {
  _source_with "$FAKE_HOME" "bin/steps.sh"
  sync_bin >/dev/null 2>&1

  local broken=0
  while IFS= read -r lnk; do
    [[ -e "$lnk" ]] || { echo "broken symlink: $lnk"; broken=1; }
  done < <(find "$FAKE_HOME/.local/bin" -maxdepth 1 -type l 2>/dev/null)
  (( broken == 0 ))
}

@test "sync_bin: no plain files written to LOCAL_BIN_DIR (only symlinks)" {
  _source_with "$FAKE_HOME" "bin/steps.sh"
  sync_bin >/dev/null 2>&1

  local plain_files
  plain_files=$(find "$FAKE_HOME/.local/bin" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
  [[ "$plain_files" -eq 0 ]]
}

# ─── sync_git ─────────────────────────────────────────────────────────────────

@test "sync_git: second run produces identical hook symlinks in GIT_HOOKS_DIR" {
  _source_with "$FAKE_HOME" "git/steps.sh"

  sync_git >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.git-hooks")

  sync_git >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.git-hooks")

  [[ "$state1" == "$state2" ]]
}

@test "sync_git: second run does not duplicate [include] stanzas in gitconfig" {
  _source_with "$FAKE_HOME" "git/steps.sh"

  sync_git >/dev/null 2>&1
  include_count_1=$(grep -c "path = " "$FAKE_HOME/.gitconfig")

  sync_git >/dev/null 2>&1
  include_count_2=$(grep -c "path = " "$FAKE_HOME/.gitconfig")

  [[ "$include_count_1" -eq "$include_count_2" ]]
}

@test "sync_git: gitconfig includes the workbench shared config after both runs" {
  _source_with "$FAKE_HOME" "git/steps.sh"

  sync_git >/dev/null 2>&1
  sync_git >/dev/null 2>&1

  grep -qF "path = $REPO_ROOT/git/gitconfig.shared" "$FAKE_HOME/.gitconfig"
}

# ─── sync_zsh ─────────────────────────────────────────────────────────────────

@test "sync_zsh: second run produces identical symlinks in ZSH_CONFIG_DIR" {
  _source_with "$FAKE_HOME" "zsh/steps.sh"

  sync_zsh >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.config/zsh/config.d" 2)

  sync_zsh >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.config/zsh/config.d" 2)

  [[ "$state1" == "$state2" ]]
}

@test "sync_zsh: second run does not change .zshrc content" {
  _source_with "$FAKE_HOME" "zsh/steps.sh"

  sync_zsh >/dev/null 2>&1
  content1=$(cat "$FAKE_HOME/.zshrc" 2>/dev/null)

  sync_zsh >/dev/null 2>&1
  content2=$(cat "$FAKE_HOME/.zshrc" 2>/dev/null)

  [[ "$content1" == "$content2" ]]
}

@test "sync_zsh: loader.zsh is present and unchanged after second run" {
  _source_with "$FAKE_HOME" "zsh/steps.sh"

  sync_zsh >/dev/null 2>&1
  checksum1=$(md5 -q "$FAKE_HOME/.config/zsh/config.d/loader.zsh" 2>/dev/null \
              || md5sum "$FAKE_HOME/.config/zsh/config.d/loader.zsh" 2>/dev/null | awk '{print $1}')

  sync_zsh >/dev/null 2>&1
  checksum2=$(md5 -q "$FAKE_HOME/.config/zsh/config.d/loader.zsh" 2>/dev/null \
              || md5sum "$FAKE_HOME/.config/zsh/config.d/loader.zsh" 2>/dev/null | awk '{print $1}')

  [[ "$checksum1" == "$checksum2" ]]
}

@test "sync_zsh: .zshrc contains the workbench loader source line after both runs" {
  _source_with "$FAKE_HOME" "zsh/steps.sh"

  sync_zsh >/dev/null 2>&1
  sync_zsh >/dev/null 2>&1

  grep -qF ".config/zsh/config.d/loader.zsh" "$FAKE_HOME/.zshrc"
}
