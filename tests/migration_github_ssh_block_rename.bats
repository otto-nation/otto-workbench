#!/usr/bin/env bats
# Tests for git/migrations/20260824-github-ssh-block-rename.sh — removes the
# superseded github-ssh-443 block from ~/.ssh/config so the github-ssh block
# step_github_ssh writes is the only one deciding the route.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  MIGRATION="$REPO_ROOT/git/migrations/20260824-github-ssh-block-rename.sh"
  FAKE_SSH="$(mktemp -d)"
  SSH_CONFIG="$FAKE_SSH/config"
}

teardown() {
  rm -rf "$FAKE_SSH"
  common_teardown
}

# Runs the migration against SSH_CONFIG with the ui.sh helpers stubbed out.
# Sources the file and then calls its function, which is what the framework
# does (lib/migrations.sh — _source_migration, then "$fn_name"), and reads the
# exit status the framework reads to decide what to record.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    MIGRATION_NOOP=3
    SSH_CONFIG_FILE="$2"
    . "$1"
    migration_20260824_github_ssh_block_rename
  ' _ "$MIGRATION" "$SSH_CONFIG"
}

# _legacy_config — an ~/.ssh/config from a machine that opted into port 443
# under the old markers.
_legacy_config() {
  cat > "$SSH_CONFIG" <<'EOF'
Include ~/.orbstack/ssh/config

# >>> otto-workbench: github-ssh-443 >>>
# Some networks block or intermittently reset outbound TCP/22.
Host github.com
  Hostname ssh.github.com
  Port 443
# <<< otto-workbench: github-ssh-443 <<<

Host myserver
  User me
EOF
}

@test "removes the legacy 443 block" {
  _legacy_config

  run _run_migration

  [ "$status" -eq 0 ]
  run grep 'github-ssh-443' "$SSH_CONFIG"
  [ "$status" -ne 0 ]
  run grep 'Port 443' "$SSH_CONFIG"
  [ "$status" -ne 0 ]
}

@test "leaves the user's own entries in place" {
  _legacy_config

  run _run_migration

  [ "$status" -eq 0 ]
  grep -q '^Include ~/.orbstack/ssh/config' "$SSH_CONFIG"
  grep -q '^Host myserver' "$SSH_CONFIG"
  grep -q '  User me' "$SSH_CONFIG"
}

@test "leaves the current github-ssh block alone" {
  cat > "$SSH_CONFIG" <<'EOF'
# >>> otto-workbench: github-ssh >>>
Host github.com
  ServerAliveInterval 30
  ServerAliveCountMax 10
# <<< otto-workbench: github-ssh <<<

# >>> otto-workbench: github-ssh-443 >>>
Host github.com
  Hostname ssh.github.com
  Port 443
# <<< otto-workbench: github-ssh-443 <<<
EOF

  run _run_migration

  [ "$status" -eq 0 ]
  grep -q '  ServerAliveInterval 30' "$SSH_CONFIG"
  run grep 'github-ssh-443' "$SSH_CONFIG"
  [ "$status" -ne 0 ]
}

@test "writes the config with owner-only permissions" {
  _legacy_config
  chmod 644 "$SSH_CONFIG"

  run _run_migration

  [ "$status" -eq 0 ]
  run file_mode "$SSH_CONFIG"
  [ "$output" = "600" ]
}

@test "a second run is a no-op" {
  _legacy_config
  _run_migration
  local after_first
  after_first=$(cat "$SSH_CONFIG")

  run _run_migration

  [ "$status" -eq 3 ]
  [ "$(cat "$SSH_CONFIG")" = "$after_first" ]
}

@test "no-op on a machine that never opted into 443" {
  printf 'Host *\n  UseKeychain yes\n' > "$SSH_CONFIG"
  local before
  before=$(cat "$SSH_CONFIG")

  run _run_migration

  [ "$status" -eq 3 ]
  [ "$(cat "$SSH_CONFIG")" = "$before" ]
}

@test "no-op when there is no ssh config at all" {
  run _run_migration

  [ "$status" -eq 3 ]
  [ ! -f "$SSH_CONFIG" ]
}

@test "refuses to strip a block left open by a hand edit" {
  _legacy_config
  perl -ni -e 'print unless /^# <<< otto-workbench/' "$SSH_CONFIG"
  local before
  before=$(cat "$SSH_CONFIG")

  run _run_migration

  [ "$status" -eq 3 ]
  [[ "$output" == *"no end marker"* ]]
  [ "$(cat "$SSH_CONFIG")" = "$before" ]
}
