#!/usr/bin/env bats
# Tests for lib/ui.sh facade and sub-module loading.
# Verifies that the decomposed modules load correctly via the facade.

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
}

teardown() {
  common_teardown
}

# ─── Facade loads all modules ───────────────────────────────────────────────

@test "ui.sh facade makes output functions available" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && type info && type success && type warn && type err"
  [ "$status" -eq 0 ]
}

@test "ui.sh facade makes prompt functions available" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && type confirm && type select_menu && type conf_get"
  [ "$status" -eq 0 ]
}

@test "ui.sh facade makes file operation functions available" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && type install_symlink && type install_file && type symlink_dir"
  [ "$status" -eq 0 ]
}

@test "ui.sh facade makes setup functions available" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && type require_command && type install_cask && type register_step"
  [ "$status" -eq 0 ]
}

@test "ui.sh facade loads constants" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && [[ -n \"\$WORKBENCH_DIR\" ]]"
  [ "$status" -eq 0 ]
}

# ─── Individual modules can be sourced standalone ───────────────────────────

@test "output.sh can be sourced independently" {
  run bash -c ". '$REPO_ROOT/lib/output.sh' && type info && type warn && type err"
  [ "$status" -eq 0 ]
}

@test "prompts.sh can be sourced independently" {
  run bash -c ". '$REPO_ROOT/lib/prompts.sh' && type confirm && type select_menu"
  [ "$status" -eq 0 ]
}

@test "files.sh can be sourced independently" {
  run bash -c ". '$REPO_ROOT/lib/files.sh' && type install_symlink && type copy_dir"
  [ "$status" -eq 0 ]
}

@test "setup.sh can be sourced independently" {
  run bash -c ". '$REPO_ROOT/lib/setup.sh' && type require_command && type run_steps"
  [ "$status" -eq 0 ]
}

# ─── Include guards prevent double-sourcing ─────────────────────────────────

@test "output.sh include guard prevents double-sourcing" {
  run bash -c "
    . '$REPO_ROOT/lib/output.sh'
    . '$REPO_ROOT/lib/output.sh'
    echo 'ok'
  "
  [ "$status" -eq 0 ]
  [[ "$output" == "ok" ]]
}

@test "sourcing ui.sh then individual module is safe" {
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/output.sh'
    . '$REPO_ROOT/lib/files.sh'
    echo 'ok'
  "
  [ "$status" -eq 0 ]
  [[ "$output" == "ok" ]]
}

# ─── Bash version guard ────────────────────────────────────────────────────

@test "output.sh rejects bash older than 4.3" {
  # BASH_VERSINFO is readonly — can't override. Test with /bin/bash (macOS 3.2) if available.
  [[ -x /bin/bash ]] || skip "/bin/bash not available"
  local old_version
  old_version=$(/bin/bash --version | head -1)
  [[ "$old_version" == *"version 3."* || "$old_version" == *"version 4.0"* || "$old_version" == *"version 4.1"* || "$old_version" == *"version 4.2"* ]] \
    || skip "/bin/bash is already 4.3+ ($old_version)"

  run /bin/bash -c ". '$REPO_ROOT/lib/output.sh' 2>&1"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Bash 4.3+ required"* ]]
}

@test "output.sh version guard contains helpful message" {
  # Verify the guard text is present in the source (even if we can't trigger it)
  grep -q "Bash 4.3+ required" "$REPO_ROOT/lib/output.sh"
  grep -q "brew install bash" "$REPO_ROOT/lib/output.sh"
}

@test "output.sh accepts current bash" {
  run bash -c ". '$REPO_ROOT/lib/output.sh' && echo ok"
  [ "$status" -eq 0 ]
  [[ "$output" == "ok" ]]
}

# ─── Nameref regression — local -n must work in all lib modules ────────────

@test "collect_registries uses namerefs without error" {
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/registries.sh'
    arr=()
    collect_registries arr '$REPO_ROOT'
    echo \"count=\${#arr[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"count="* ]]
}

@test "discover_step_files uses namerefs without error" {
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/components.sh'
    files=()
    discover_step_files files
    echo \"count=\${#files[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"count="* ]]
}

@test "discover_migration_dirs uses namerefs without error" {
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/components.sh'
    dirs=()
    discover_migration_dirs dirs
    echo \"count=\${#dirs[@]}\"
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"count="* ]]
}

# ─── Zsh compatibility ─────────────────────────────────────────────────────

@test "output.sh works when sourced from zsh" {
  command -v zsh &>/dev/null || skip "zsh not available"
  run zsh -c ". '$REPO_ROOT/lib/output.sh' && info 'hello from zsh'"
  [ "$status" -eq 0 ]
  [[ "$output" == *"hello from zsh"* ]]
}

@test "ui.sh facade works when sourced from zsh (output only)" {
  command -v zsh &>/dev/null || skip "zsh not available"
  run zsh -c ". '$REPO_ROOT/lib/ui.sh' && info 'hello from zsh'"
  [ "$status" -eq 0 ]
  [[ "$output" == *"hello from zsh"* ]]
}
