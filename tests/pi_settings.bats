#!/usr/bin/env bats
# Tests for step_pi_settings in ai/pi/steps.sh — merging the workbench's managed
# keys into Pi's live global settings, with the shared package gated on the
# machine's membership of the org that hosts it.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  AGENT_DIR="$TMPDIR/pi/agent"
  LIVE="$AGENT_DIR/settings.json"
  TEMPLATE="$TMPDIR/template.json"
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  _write_template '["git:github.com/usemaximum/pi-extensions"]'
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_template PACKAGES_JSON — the managed template the step reads.
_write_template() {
  cat > "$TEMPLATE" << JSON
{
  "defaultProvider": "google-vertex-claude",
  "defaultModel": "claude-opus-4-6",
  "packages": $1
}
JSON
}

# _write_live JSON — the settings file Pi and the operator already wrote.
_write_live() {
  mkdir -p "$AGENT_DIR"
  printf '%s\n' "$1" > "$LIVE"
}

# _stub_gh BODY — a gh on PATH whose whole behaviour is BODY.
_stub_gh() {
  cat > "$BIN/gh" << SCRIPT
#!/usr/bin/env bash
$1
SCRIPT
  chmod +x "$BIN/gh"
  PATH="$BIN:$PATH"
}

# _hide_gh — a PATH with no gh on it, which is one of the ways a verdict comes
# back unknown. jq is symlinked in because the step needs it either way, and
# bash because /bin/bash on macOS is 3.2 and has no namerefs — a PATH narrow
# enough to lose gh would otherwise run the step under a shell it predates.
_hide_gh() {
  ln -sf "$(command -v jq)" "$BIN/jq"
  ln -sf "$BASH" "$BIN/bash"
  PATH="$BIN:/usr/bin:/bin"
}

# _run_step — runs step_pi_settings against the sandbox with the ui helpers
# stubbed. Runs in its own bash so the step's skip() does not displace bats'.
_run_step() {
  bash -c '
    set -e
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    err()     { echo "ERR $*"; }
    skip()    { echo "SKIP $*"; }
    PI_AGENT_DIR="$2"
    PI_SETTINGS_FILE="$2/settings.json"
    PI_SETTINGS_SRC="$3"
    PI_SYNC_SETTINGS_JQ="$1/ai/pi/sync-settings.jq"
    . "$1/ai/pi/steps.sh"
    step_pi_settings
  ' _ "$REPO_ROOT" "$AGENT_DIR" "$TEMPLATE"
}

# _live FILTER — the filter's answer against the merged settings file.
_live() {
  jq -r "$1" "$LIVE"
}

@test "writes to the agent path Pi actually reads" {
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ -f "$LIVE" ]
  [ ! -e "$TMPDIR/pi/settings.json" ]
}

@test "seeds the managed defaults into a machine that has none" {
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-4-6" ]
  [ "$(_live '.defaultProvider')" = "google-vertex-claude" ]
}

@test "a value already in the live file is seeded past, not overridden" {
  # Whatever set it first keeps it — an extension, `pi config`, or Ctrl+S in
  # /model. Only the keys the file does not carry are written.
  _write_live '{"defaultModel": "claude-sonnet-5"}'
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-sonnet-5" ]
  [ "$(_live '.defaultProvider')" = "google-vertex-claude" ]
}

@test "declares the shared package for a member of its org" {
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "git:github.com/usemaximum/pi-extensions" ]
}

@test "keeps a package the operator installed themselves" {
  _write_live '{"packages": ["npm:pi-thing"]}'
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "2" ]
  [ "$(_live '.packages[0]')" = "npm:pi-thing" ]
}

@test "a pinned ref of the same package is left as the operator pinned it" {
  _write_live '{"packages": ["git:github.com/usemaximum/pi-extensions@v2"]}'
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "1" ]
  [ "$(_live '.packages[0]')" = "git:github.com/usemaximum/pi-extensions@v2" ]
}

@test "an object-form entry carrying filters is not duplicated by the plain source" {
  _write_live '{"packages": [{"source": "git:github.com/usemaximum/pi-extensions", "tools": ["web_fetch"]}]}'
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "1" ]
  [ "$(_live '.packages[0].tools[0]')" = "web_fetch" ]
}

@test "withdraws the package when the org refuses the membership lookup" {
  # A non-member cannot clone a private repo, so leaving the entry in place
  # buys a failing clone on every Pi startup.
  _write_live '{"packages": ["git:github.com/usemaximum/pi-extensions"]}'
  _stub_gh 'echo "gh: Not Found (HTTP 404)" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "0" ]
  [[ "$output" == *"no active usemaximum membership"* ]]
}

@test "a pending invitation is not membership" {
  _stub_gh 'echo pending'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "0" ]
}

@test "an unverifiable membership leaves a working package alone" {
  # A sync run offline must not withdraw what already works.
  _write_live '{"packages": ["git:github.com/usemaximum/pi-extensions"]}'
  _stub_gh 'echo "dial tcp: lookup api.github.com: no such host" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "git:github.com/usemaximum/pi-extensions" ]
  [[ "$output" == *"could not verify usemaximum membership"* ]]
}

@test "an unverifiable membership does not install the package either" {
  _stub_gh 'echo "dial tcp: lookup api.github.com: no such host" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "0" ]
}

@test "a machine with no gh reaches no verdict" {
  _hide_gh

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"could not verify usemaximum membership"* ]]
}

@test "a package with no GitHub org is not gated on membership" {
  _write_template '["npm:pi-thing"]'
  _hide_gh

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "npm:pi-thing" ]
}

@test "no packages key is invented when there is nothing to record" {
  _stub_gh 'echo "gh: Not Found (HTTP 404)" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live 'has("packages")')" = "false" ]
}

@test "a second run changes nothing" {
  _stub_gh 'echo active'

  run _run_step
  [ "$status" -eq 0 ]
  local first
  first="$(cat "$LIVE")"

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$LIVE")" = "$first" ]
}

@test "the shipped template declares the pi-extensions package" {
  run jq -r '.packages[0]' "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
  [ "$output" = "git:github.com/usemaximum/pi-extensions" ]
}
