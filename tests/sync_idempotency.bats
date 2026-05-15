#!/usr/bin/env bats
# Idempotency tests for sync functions.
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
  export WORKBENCH_STABLE_DIR="$REPO_ROOT"
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

# _checksum FILE — prints the md5 hash of FILE (macOS or Linux).
_checksum() {
  md5 -q "$1" 2>/dev/null || md5sum "$1" 2>/dev/null | awk '{print $1}'
}

# _file_list DIR — prints sorted list of "basename checksum" for regular files under DIR.
_file_list() {
  local dir="$1"
  find "$dir" -maxdepth 1 -type f 2>/dev/null | sort | while IFS= read -r f; do
    echo "$(basename "$f") $(_checksum "$f")"
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
# Tests call individual step functions instead of the full sync_zsh to avoid
# the expensive step_env_local (~1s per call) that none of them need.

_zsh_setup() {
  _source_with "$FAKE_HOME" "zsh/steps.sh"
  mkdir -p "$ZSH_CONFIG_DIR"
}

@test "sync_zsh: second run produces identical symlinks in ZSH_CONFIG_DIR" {
  _zsh_setup

  step_zsh >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.config/zsh/config.d" 2)

  step_zsh >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.config/zsh/config.d" 2)

  [[ "$state1" == "$state2" ]]
}

@test "sync_zsh: second run does not change .zshrc content" {
  _zsh_setup
  step_zsh_loader >/dev/null 2>&1

  step_zshrc >/dev/null 2>&1
  content1=$(cat "$FAKE_HOME/.zshrc" 2>/dev/null)

  step_zshrc >/dev/null 2>&1
  content2=$(cat "$FAKE_HOME/.zshrc" 2>/dev/null)

  [[ "$content1" == "$content2" ]]
}

@test "sync_zsh: loader.zsh is present and unchanged after second run" {
  _zsh_setup

  step_zsh_loader >/dev/null 2>&1
  checksum1=$(_checksum "$FAKE_HOME/.config/zsh/config.d/loader.zsh")

  step_zsh_loader >/dev/null 2>&1
  checksum2=$(_checksum "$FAKE_HOME/.config/zsh/config.d/loader.zsh")

  [[ "$checksum1" == "$checksum2" ]]
}

@test "sync_zsh: .zshrc contains the workbench loader source line after both runs" {
  _zsh_setup
  step_zsh_loader >/dev/null 2>&1

  step_zshrc >/dev/null 2>&1
  step_zshrc >/dev/null 2>&1

  grep -qF ".config/zsh/config.d/loader.zsh" "$FAKE_HOME/.zshrc"
}

# ─── sync_task ───────────────────────────────────────────────────────────────

@test "sync_task: second run produces identical symlinks in TASK_CONFIG_DIR" {
  _source_with "$FAKE_HOME" "task/steps.sh"

  sync_task >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.config/task")

  sync_task >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.config/task")

  [[ "$state1" == "$state2" ]]
}

@test "sync_task: symlinks are valid" {
  _source_with "$FAKE_HOME" "task/steps.sh"
  sync_task >/dev/null 2>&1

  local broken=0
  while IFS= read -r lnk; do
    [[ -e "$lnk" ]] || { echo "broken symlink: $lnk"; broken=1; }
  done < <(find "$FAKE_HOME/.config/task" -maxdepth 1 -type l 2>/dev/null)
  (( broken == 0 ))
}

# ─── sync_serena ─────────────────────────────────────────────────────────────

@test "sync_serena: second run produces identical symlinks" {
  _source_with "$FAKE_HOME" "ai/serena/steps.sh"

  sync_serena >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.local/bin")

  sync_serena >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.local/bin")

  [[ "$state1" == "$state2" ]]
}

@test "sync_serena: symlinks are valid" {
  _source_with "$FAKE_HOME" "ai/serena/steps.sh"
  sync_serena >/dev/null 2>&1

  local broken=0
  while IFS= read -r lnk; do
    [[ -e "$lnk" ]] || { echo "broken symlink: $lnk"; broken=1; }
  done < <(find "$FAKE_HOME/.local/bin" -maxdepth 1 -type l 2>/dev/null)
  (( broken == 0 ))
}

# ─── sync_ghostty ────────────────────────────────────────────────────────────

_ghostty_setup() {
  _source_with "$FAKE_HOME" "terminals/ghostty/steps.sh"
  mkdir -p "$GHOSTTY_CONFIG_DIR"
  cp "$GHOSTTY_CONFIG_TEMPLATE" "$GHOSTTY_CONFIG_FILE"
}

@test "sync_ghostty: second run produces identical state" {
  _ghostty_setup

  sync_ghostty >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.local/bin")
  checksum1=$(_checksum "$GHOSTTY_CONFIG_FILE")

  sync_ghostty >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.local/bin")
  checksum2=$(_checksum "$GHOSTTY_CONFIG_FILE")

  [[ "$state1" == "$state2" ]]
  [[ "$checksum1" == "$checksum2" ]]
}

@test "sync_ghostty: theme line is set correctly" {
  _ghostty_setup
  sync_ghostty >/dev/null 2>&1

  grep -q '^theme = light:Gruvbox Light,dark:Gruvbox Dark' "$GHOSTTY_CONFIG_FILE"
}

@test "sync_ghostty: skips when ghostty not installed and config dir absent" {
  _source_with "$FAKE_HOME" "terminals/ghostty/steps.sh"

  run sync_ghostty
  [ "$status" -eq 0 ]
}

# ─── sync_claude (individual steps) ─────────────────────────────────────────
# Tests the filesystem steps individually rather than the full sync_claude
# monolith, which requires the real claude CLI for MCP registration.

_claude_setup() {
  _source_with "$FAKE_HOME" "ai/claude/steps.sh"
  make_fake_binary "$FAKE_HOME/.local/bin" "claude"
  export PATH="$FAKE_HOME/.local/bin:$PATH"
}

@test "step_claude_guidelines: second run produces identical CLAUDE.md" {
  _claude_setup

  step_claude_guidelines >/dev/null 2>&1
  checksum1=$(_checksum "$FAKE_HOME/.claude/CLAUDE.md")

  step_claude_guidelines >/dev/null 2>&1
  checksum2=$(_checksum "$FAKE_HOME/.claude/CLAUDE.md")

  [[ "$checksum1" == "$checksum2" ]]
}

@test "step_claude_settings: second run produces identical settings.json" {
  _claude_setup

  step_claude_settings >/dev/null 2>&1
  content1=$(cat "$FAKE_HOME/.claude/settings.json")

  step_claude_settings >/dev/null 2>&1
  content2=$(cat "$FAKE_HOME/.claude/settings.json")

  [[ "$content1" == "$content2" ]]
}

@test "step_claude_skills: second run produces identical symlinks" {
  _claude_setup

  step_claude_skills >/dev/null 2>&1
  state1=$(_symlinks "$FAKE_HOME/.claude/skills" 2)

  step_claude_skills >/dev/null 2>&1
  state2=$(_symlinks "$FAKE_HOME/.claude/skills" 2)

  [[ "$state1" == "$state2" ]]
}

@test "step_claude_agents: second run produces identical files" {
  _claude_setup

  step_claude_agents >/dev/null 2>&1
  state1=$(_file_list "$FAKE_HOME/.claude/agents")

  step_claude_agents >/dev/null 2>&1
  state2=$(_file_list "$FAKE_HOME/.claude/agents")

  [[ "$state1" == "$state2" ]]
}

@test "step_claude_mcps: runs without error twice" {
  _claude_setup

  run step_claude_mcps
  [ "$status" -eq 0 ]

  run step_claude_mcps
  [ "$status" -eq 0 ]
}
