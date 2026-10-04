#!/usr/bin/env bats
# Tests for GitHub SSH setup and the connection keepalive.

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"

  # Source steps.sh for access to helper functions
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/git/steps.sh"
}

teardown() {
  cd "$ORIG_DIR" || return 1
  common_teardown
}

# ── GitHub SSH ──────────────────────────────────────────────────────────────

# The stand-in host key the known_hosts tests copy and look for. Real in shape
# and nothing else — the step never verifies a key, it moves the ones already
# trusted under a second name.
SSH_GITHUB_FAKE_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAFAKEKEY"

# _ssh_github_setup [KEY_VALUE] — point the step at a scratch ssh directory
# standing in for ~/.ssh, and a temp global config holding KEY_VALUE, from a
# directory that is not a git repo.
#
# The config root is exported rather than the file path overridden, because
# wb_config_get resolves the scopes in a child process now — lib/config_cli.py,
# which builds the global path from WORKBENCH_CONFIG_DIR the same way
# constants.sh does. A WORKBENCH_CONFIG_FILE set only in this shell would leave
# that child reading the real one.
#
# The cd matters for the same reason it always did: the project scope resolves
# from the working directory, and inside this repo that is a .workbench.yml the
# test does not control.
_ssh_github_setup() {
  SSH_DIR="$TMPDIR/ssh"
  SSH_CONFIG_FILE="$SSH_DIR/config"
  SSH_KNOWN_HOSTS_FILE="$SSH_DIR/known_hosts"
  mkdir -p "$SSH_DIR"

  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  WORKBENCH_CONFIG_FILE="$WORKBENCH_CONFIG_DIR/$WORKBENCH_CONFIG_NAME"
  if [[ -n "${1:-}" ]]; then
    printf 'github:\n  ssh_over_443: %s\n' "$1" > "$WORKBENCH_CONFIG_FILE"
  fi

  mkdir -p "$TMPDIR/not-a-repo"
  cd "$TMPDIR/not-a-repo" || return 1
}

# _assert_keepalive_present — fail unless SSH_CONFIG_FILE carries both
# keepalive lines the managed block always writes.
_assert_keepalive_present() {
  grep -q '  ServerAliveInterval 30' "$SSH_CONFIG_FILE"
  grep -q '  ServerAliveCountMax 10' "$SSH_CONFIG_FILE"
}

# _ssh_github_user_config — an ~/.ssh/config shaped like a real one: includes that
# must stay at the top, then a catch-all Host block.
_ssh_github_user_config() {
  cat > "$SSH_CONFIG_FILE" <<'EOF'
Include ~/.orbstack/ssh/config

Host myserver
  User me

Host *
  UseKeychain yes
EOF
}

@test "github ssh block lands ahead of the first Host block" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  local block_line host_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  host_line=$(grep -n '^Host myserver' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$host_line" ]
}

@test "github ssh block lands after the Include lines" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  local include_line block_line
  include_line=$(grep -n '^Include' "$SSH_CONFIG_FILE" | cut -d: -f1)
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$include_line" -lt "$block_line" ]
}

@test "github ssh preserves the user's own entries" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  grep -q '^Include ~/.orbstack/ssh/config' "$SSH_CONFIG_FILE"
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh appends when the config has no Host block" {
  _ssh_github_setup true
  printf 'Include ~/.colima/ssh_config\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  grep -q '^Include ~/.colima/ssh_config' "$SSH_CONFIG_FILE"
  grep -q 'Hostname ssh.github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh creates the config when there is none" {
  _ssh_github_setup true

  step_github_ssh

  [ -f "$SSH_CONFIG_FILE" ]
  grep -q 'Port 443' "$SSH_CONFIG_FILE"
}

@test "github ssh is idempotent" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh
  step_github_ssh

  local count
  count=$(grep -c 'Hostname ssh.github.com' "$SSH_CONFIG_FILE")
  [ "$count" -eq 1 ]
}

@test "github ssh keeps the direct route when the key is unset" {
  _ssh_github_setup
  _ssh_github_user_config

  step_github_ssh

  run grep 'ssh.github.com' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  grep -q '^Host github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh drops the 443 lines when the key flips to false" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  run grep 'ssh.github.com' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  run grep 'Port 443' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  [ "$(grep -c '^Host github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
}

@test "github ssh keeps the keepalive when the key flips to false" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  _assert_keepalive_present
}

@test "github ssh flipping the key off leaves the rest of the config intact" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  grep -q '^Include ~/.orbstack/ssh/config' "$SSH_CONFIG_FILE"
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh writes the config with owner-only permissions" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  [ "$(file_mode "$SSH_CONFIG_FILE")" = "600" ]
}

@test "github ssh copies the github.com host keys under the 443 name" {
  _ssh_github_setup true
  printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY" > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  grep -qxF "[ssh.github.com]:443 $SSH_GITHUB_FAKE_KEY" "$SSH_KNOWN_HOSTS_FILE"
}

@test "github ssh does not duplicate an existing 443 known_hosts entry" {
  _ssh_github_setup true
  {
    printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY"
    printf '[ssh.github.com]:443 %s\n' "$SSH_GITHUB_FAKE_KEY"
  } > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  local count
  count=$(grep -c 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE")
  [ "$count" -eq 1 ]
}

@test "github ssh warns instead of guessing when github.com is unknown" {
  _ssh_github_setup true
  printf 'gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAOTHERKEY\n' > "$SSH_KNOWN_HOSTS_FILE"

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"no github.com entry"* ]]
  run grep 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE"
  [ "$status" -ne 0 ]
}

@test "github ssh block lands ahead of a lowercase host block" {
  _ssh_github_setup true
  printf 'host *\n  UseKeychain yes\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  local block_line wildcard_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  wildcard_line=$(grep -n '^host \*' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$wildcard_line" ]
}

@test "github ssh block lands ahead of a leading Match block" {
  _ssh_github_setup true
  printf 'Match host gitlab.com\n  User git\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  local block_line match_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  match_line=$(grep -n '^Match host gitlab.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$match_line" ]
}

@test "github ssh rewrites a block whose text has drifted from the template" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  # Stand in for a machine that installed an older wording of the block.
  perl -pi -e 's/^  Port 443$/  Port 443\n  # stale line from an older release/' \
    "$SSH_CONFIG_FILE"
  step_github_ssh

  run grep 'stale line from an older release' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  [ "$(grep -c 'Hostname ssh.github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
}

@test "github ssh refuses to strip a block left open by a hand edit" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  perl -ni -e 'print unless /^# <<< otto-workbench/' "$SSH_CONFIG_FILE"
  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"no end marker"* ]]
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh warns when the user declares github.com themselves" {
  _ssh_github_setup true
  printf 'Host github.com\n  User git\n\nHost *\n  UseKeychain yes\n' > "$SSH_CONFIG_FILE"

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"outside the managed block"* ]]
}

@test "github ssh stays quiet about github.com when only its own block declares it" {
  _ssh_github_setup true
  _ssh_github_user_config

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" != *"outside the managed block"* ]]
}

# ── Keepalive ───────────────────────────────────────────────────────────────

@test "github ssh writes the keepalive with the 443 key off" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh

  _assert_keepalive_present
}

@test "github ssh writes the keepalive alongside the 443 routing" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  grep -q '  Port 443' "$SSH_CONFIG_FILE"
  _assert_keepalive_present
}

@test "github ssh keepalive block lands ahead of the first Host block" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh

  local block_line host_line
  block_line=$(grep -n 'ServerAliveInterval' "$SSH_CONFIG_FILE" | cut -d: -f1)
  host_line=$(grep -n '^Host myserver' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$host_line" ]
}

@test "github ssh keepalive leaves no idle gap a pre-push run can outlast" {
  # The failure this guards is a push dropped while pre-push runs: git holds the
  # connection open from before the hook to after it, and the three gates take
  # over five minutes on a developer machine. The interval is the longest the
  # socket goes without traffic, so it is the value that has to stay well inside
  # the remote's idle timeout — a hook of any length is covered as long as the
  # keepalives keep arriving. The count is how many may go unanswered before ssh
  # calls the connection dead, which buys tolerance for a lossy network rather
  # than for a slow hook.
  _ssh_github_setup false

  local interval count
  interval=$(_ssh_github_block false | awk '/ServerAliveInterval/ { print $2 }')
  count=$(_ssh_github_block false | awk '/ServerAliveCountMax/ { print $2 }')

  [ "$interval" -gt 0 ]
  [ "$interval" -le 60 ]
  [ "$count" -ge 2 ]
}

@test "github ssh creates the config for the keepalive with the 443 key off" {
  _ssh_github_setup false

  step_github_ssh

  [ -f "$SSH_CONFIG_FILE" ]
  grep -q '  ServerAliveInterval 30' "$SSH_CONFIG_FILE"
}

@test "github ssh is idempotent with the 443 key off" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh
  step_github_ssh

  [ "$(grep -c 'ServerAliveInterval' "$SSH_CONFIG_FILE")" -eq 1 ]
}

@test "github ssh adopts the 443 routing without a second block" {
  _ssh_github_setup false
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: true\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  [ "$(grep -c '^Host github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
  [ "$(grep -c 'ServerAliveInterval' "$SSH_CONFIG_FILE")" -eq 1 ]
  grep -q '  Hostname ssh.github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh does not touch known_hosts while the 443 key is off" {
  _ssh_github_setup false
  printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY" > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  run grep 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE"
  [ "$status" -ne 0 ]
}
