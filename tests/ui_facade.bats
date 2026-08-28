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

@test "ui.sh facade makes the git environment helper available" {
  run bash -c ". '$REPO_ROOT/lib/ui.sh' && type git_env_clear"
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

@test "gitenv.sh can be sourced independently" {
  run bash -c ". '$REPO_ROOT/lib/gitenv.sh' && type git_env_clear"
  [ "$status" -eq 0 ]
}

@test "git_env_clear drops every inherited git override" {
  # Exported, because that is how a hook hands them down — an unexported
  # assignment would pass even if the function unset nothing.
  run bash -c "
    export GIT_DIR=/tmp/x.git GIT_WORK_TREE=/tmp/x GIT_INDEX_FILE=/tmp/x.idx
    export GIT_OBJECT_DIRECTORY=/tmp/o GIT_ALTERNATE_OBJECT_DIRECTORIES=/tmp/a
    . '$REPO_ROOT/lib/gitenv.sh'
    git_env_clear
    env | grep -cE '^(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|GIT_OBJECT_DIRECTORY|GIT_ALTERNATE_OBJECT_DIRECTORIES)='
  "
  [ "$output" -eq 0 ]
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

# ─── skip name collision with bats ─────────────────────────────────────────

@test "bats_skip survives a setup that sources lib/ui.sh" {
  # output.sh's skip() takes the name from bats, so a guard written as
  # `skip "reason"` in such a file prints its reason and returns 0 — the body
  # runs on and fails on the dependency it just reported missing. Run the pair
  # in a nested bats so this case reports the outcome instead of being skipped
  # itself.
  local probe="$BATS_TEST_TMPDIR/skip_collision.bats"
  cat > "$probe" <<EOF
#!/usr/bin/env bats

setup() {
  load '$REPO_ROOT/tests/test_helper'
  REPO_ROOT='$REPO_ROOT'
  . "\$REPO_ROOT/lib/ui.sh"
}

@test "a guard reached after ui.sh is sourced marks the test skipped" {
  bats_skip "dependency missing"
  false
}

@test "the workbench skip still answers to its own name" {
  run skip "workbench label"
  [ "\$status" -eq 0 ]
  [[ "\$output" == *"workbench label"* ]]
}
EOF

  run bats "$probe"

  [ "$status" -eq 0 ]
  [[ "$output" == *"# skip dependency missing"* ]]
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
