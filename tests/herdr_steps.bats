#!/usr/bin/env bats
# Tests for ai/herdr/steps.sh — the argv each step sends to herdr, which
# integrations it installs, and that every failure is non-fatal.
#
# herdr is stubbed: a real `herdr update` would replace the developer's own
# binary from a test.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  ARGV="$TMPDIR/herdr-argv"
  export FAKE_PI_AGENT_DIR="$TMPDIR/pi-agent"
  export FAKE_CLAUDE_DIR="$TMPDIR/claude"
}

teardown() {
  common_teardown
}

# _stub_herdr EXIT [STDOUT] — a herdr on PATH that appends its argv (one call
# per line) to $ARGV, prints STDOUT, and exits EXIT.
_stub_herdr() {
  cat > "$BIN/herdr" << SCRIPT
#!/usr/bin/env bash
echo "\$*" >> "$ARGV"
printf '%s' '${2:-}'
exit ${1:-0}
SCRIPT
  chmod +x "$BIN/herdr"
  PATH="$BIN:$PATH"
}

# _hide_herdr — a PATH with no herdr on it.
_hide_herdr() {
  ln -sf "$BASH" "$BIN/bash"
  PATH="$BIN:/usr/bin:/bin"
}

# _run FN — runs one function from ai/herdr/steps.sh with the ui helpers
# stubbed, in its own bash so the step's warn() does not displace bats'.
_run() {
  bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    err()     { echo "ERR $*"; }
    info()    { echo "INFO $*"; }
    skip()    { echo "SKIP $*"; }
    sync_header() { :; }
    install_via_installer() { echo "INSTALLER $*"; }
    WORKBENCH_DIR="$1"
    . "$1/lib/constants.sh"
    PI_AGENT_DIR="$FAKE_PI_AGENT_DIR"
    CLAUDE_DIR="$FAKE_CLAUDE_DIR"
    . "$1/ai/herdr/steps.sh"
    "$2"
  ' _ "$REPO_ROOT" "$1"
}

@test "install delegates to the vendor installer with the herdr URL" {
  local url
  url=$(sed -n 's/^HERDR_INSTALL_URL="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  run _run step_install_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"INSTALLER herdr $url Herdr"* ]]
}

@test "update runs plain herdr update, never --handoff" {
  _stub_herdr 0
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "update" ]
}

@test "a failing update warns and the step still exits 0" {
  _stub_herdr 1
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"herdr was not updated"* ]]
  [[ "$output" != *"run: herdr update"* ]]
}

@test "update warns when herdr declines and does not consume stdin" {
  cat > "$BIN/herdr" << SCRIPT
#!/usr/bin/env bash
echo "\$*" >> "$ARGV"
if read -r x; then
  printf 'READ:%s\n' "\$x" >> "$ARGV.stdin"
else
  echo EOF >> "$ARGV.stdin"
fi
echo "Herdr was not updated" >&2
exit 0
SCRIPT
  chmod +x "$BIN/herdr"
  PATH="$BIN:$PATH"

  run bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$1"
    . "$1/lib/constants.sh"
    . "$1/ai/herdr/steps.sh"
    echo "should-not-be-read" | step_update_herdr
  ' _ "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"herdr was not updated"* ]]
  [[ "$output" != *"herdr is current"* ]]
  [ "$(cat "$ARGV.stdin")" = "EOF" ]
}

@test "a brew-disabled self-update surfaces herdr's own message" {
  _stub_herdr 1 'self-update is disabled for Homebrew installs; run `brew update && brew upgrade herdr`'
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"brew upgrade herdr"* ]]
  [[ "$output" != *"run: herdr update"* ]]
}

@test "update is skipped with a warning when herdr is absent" {
  _hide_herdr
  run _run step_update_herdr
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"herdr not found"* ]]
  [ ! -e "$ARGV" ]
}

@test "integrations install pi and claude when both config dirs exist" {
  mkdir -p "$FAKE_PI_AGENT_DIR" "$FAKE_CLAUDE_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "$(printf 'integration install pi\nintegration install claude')" ]
}

@test "integrations skip pi when the Pi agent dir is missing" {
  mkdir -p "$FAKE_CLAUDE_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "integration install claude" ]
}

@test "integrations skip claude when the Claude dir is missing" {
  mkdir -p "$FAKE_PI_AGENT_DIR"
  _stub_herdr 0
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "integration install pi" ]
}

@test "a failing integration install warns and the step still exits 0" {
  mkdir -p "$FAKE_PI_AGENT_DIR" "$FAKE_CLAUDE_DIR"
  _stub_herdr 1
  run _run step_herdr_integrations
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"integration install pi"* ]]
  [[ "$output" == *"WARN"*"integration install claude"* ]]
}

@test "sync updates before installing integrations" {
  mkdir -p "$FAKE_PI_AGENT_DIR"
  _stub_herdr 0
  run _run sync_herdr
  [ "$status" -eq 0 ]
  run cat "$ARGV"
  [ "$output" = "$(printf 'update\nintegration install pi\nmachine list --json')" ]
}

@test "sync leaves a machine without herdr alone" {
  _hide_herdr
  run _run sync_herdr
  [ "$status" -eq 0 ]
  [ ! -e "$ARGV" ]
}

@test "machine note is printed when no machines are registered" {
  _stub_herdr 0 '[]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" == *"herdr: no remote machines registered — add one with: herdr machine add <ssh-host> --label <name>"* ]]
}

@test "machine note is silent when a machine is registered" {
  _stub_herdr 0 '[{"id":"m1","label":"homelab","target":"homelab","session":"default","enabled":true,"selected":false}]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent under WORKBENCH_SYNC=true" {
  _stub_herdr 0 '[]'
  WORKBENCH_SYNC=true run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
  [ ! -e "$ARGV" ]
}

@test "machine note is silent when herdr errors" {
  _stub_herdr 1 '[]'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent when herdr prints something that is not JSON" {
  _stub_herdr 0 'not json'
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "machine note is silent when herdr is absent" {
  _hide_herdr
  run _run step_herdr_machine_note
  [ "$status" -eq 0 ]
  [[ "$output" != *"no remote machines"* ]]
}

@test "install skipped when herdr is present" {
  _stub_herdr 0
  export CURL_LOG="$TMPDIR/curl.log"
  cat > "$BIN/curl" << 'SCRIPT'
#!/usr/bin/env bash
echo "$*" >> "${CURL_LOG:?}"
exit 1
SCRIPT
  chmod +x "$BIN/curl"

  run bash -c '
    set -e
    export NO_COLOR=1
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    PATH="$2"
    step_install_herdr
  ' _ "$REPO_ROOT" "$BIN:/usr/bin:/bin"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Herdr already installed"* ]]
  [ ! -e "$CURL_LOG" ]
}

@test "install prepends LOCAL_BIN_DIR when herdr is not yet on PATH" {
  local local_bin="$TMPDIR/local-bin"
  mkdir -p "$local_bin"
  run bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    LOCAL_BIN_DIR="$1"
    HERDR_INSTALL_URL="unused"
    install_via_installer() {
      printf "#!/usr/bin/env bash\nexit 0\n" > "$LOCAL_BIN_DIR/herdr"
      chmod +x "$LOCAL_BIN_DIR/herdr"
    }
    PATH="/usr/bin:/bin"
    . "$2/ai/herdr/steps.sh"
    step_install_herdr
    command -v herdr
  ' _ "$local_bin" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"$local_bin/herdr"* ]]
}
