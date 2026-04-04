#!/usr/bin/env bats
# Tests for lib/ui.sh facade and sub-module loading.
# Verifies that the decomposed modules load correctly via the facade.

setup() {
  load 'test_helper'
  export NO_COLOR=1
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
