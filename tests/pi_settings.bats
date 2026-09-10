#!/usr/bin/env bats
# Tests for step_pi_settings in ai/pi/steps.sh — merging the workbench's managed
# keys into Pi's live global settings, with each package gated on whether this
# machine can reach the repo it names.
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
  ORG="usemaximum"
  REPO="$ORG/pi-extensions"
  PKG="git:github.com/$REPO"
  _write_template "$(jq -nc --arg p "$PKG" '[$p]')"
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

# _write_live_packages ENTRY... — a live file whose packages hold ENTRY..., each
# a plain source string. Built with jq so the sources stay shell values rather
# than becoming a second spelling of $PKG inside a JSON literal.
_write_live_packages() {
  _write_live "$(jq -nc '$ARGS.positional | {packages: .}' --args "$@")"
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
#
# yq for the same reason as jq: _pi_build_models reads the registries through
# collect_model_env_vars. Both tests here reach the {} return before that,
# since ENV_LOCAL_FILE defaults to /dev/null — the symlink is so a reordering
# fails on its merits rather than on a PATH accident.
_hide_gh() {
  ln -sf "$(command -v jq)" "$BIN/jq"
  ln -sf "$(command -v yq)" "$BIN/yq"
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
    ENV_LOCAL_FILE="${ENV_LOCAL_FILE:-/dev/null}"
    LIB_SRC_DIR="$1/lib"
    # The registry root _pi_build_models collects models from. Overridable so a
    # test can point it at a fixture tree instead of the repo.
    WORKBENCH_STABLE_DIR="${WORKBENCH_STABLE_DIR:-$1}"
    . "$1/lib/env.sh"
    . "$1/ai/pi/steps.sh"
    step_pi_settings
  ' _ "$REPO_ROOT" "$AGENT_DIR" "$TEMPLATE"
}

# _live FILTER — the filter's answer against the merged settings file.
_live() {
  jq -r "$1" "$LIVE"
}

@test "writes to the agent path Pi actually reads" {
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ -f "$LIVE" ]
  [ ! -e "$TMPDIR/pi/settings.json" ]
}

@test "seeds the managed defaults into a machine that has none" {
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-4-6" ]
  [ "$(_live '.defaultProvider')" = "google-vertex-claude" ]
}

@test "template scalars override the live file" {
  # The workbench is authoritative — template values always win over whatever
  # an extension or `pi config` set.
  _write_live '{"defaultModel": "claude-sonnet-5"}'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-4-6" ]
  [ "$(_live '.defaultProvider')" = "google-vertex-claude" ]
}

@test "declares the shared package when its repo answers" {
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "$PKG" ]
}

@test "keeps a package the operator installed themselves" {
  _write_live_packages "npm:pi-thing"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "2" ]
  [ "$(_live '.packages[0]')" = "npm:pi-thing" ]
}

@test "a pinned ref of the same package is left as the operator pinned it" {
  _write_live_packages "$PKG@v2"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "1" ]
  [ "$(_live '.packages[0]')" = "$PKG@v2" ]
}

@test "an object-form entry carrying filters is not duplicated by the plain source" {
  _write_live "$(jq -nc --arg p "$PKG" '{packages: [{source: $p, tools: ["web_fetch"]}]}')"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "1" ]
  [ "$(_live '.packages[0].tools[0]')" = "web_fetch" ]
}

@test "declares a public package owned by a user account" {
  # No org to be a member of. The gate asks whether the repo answers, so a
  # personal account's public repo installs like any other.
  _write_template '["git:github.com/obra/superpowers@v6.3.0"]'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "git:github.com/obra/superpowers@v6.3.0" ]
}

@test "the probe addresses the repo, not the owner" {
  # A pinned @ref is not part of the repo's name, and probing the owner alone
  # is the membership proxy this gate replaced.
  _write_template '["git:github.com/obra/superpowers@v6.3.0"]'
  # The heredoc in _stub_gh expands at write time, so the args path is baked in
  # and only \$* is left for the stub itself to expand.
  _stub_gh "echo \"\$*\" > '$TMPDIR/gh-args'; echo '{}'"

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/gh-args")" = "api repos/obra/superpowers" ]
}

@test "withdraws the package when the repo cannot be reached" {
  # A repo this token cannot see \u2014 deleted, renamed, private, or restricted \u2014
  # cannot be cloned, so leaving the entry in place buys a failing clone on
  # every Pi startup.
  _write_live_packages "$PKG"
  _stub_gh 'echo "gh: Not Found (HTTP 404)" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "0" ]
  [[ "$output" == *"cannot reach $REPO"* ]]
}

@test "an unverifiable repo leaves a working package alone" {
  # A sync run offline must not withdraw what already works.
  _write_live_packages "$PKG"
  _stub_gh 'echo "dial tcp: lookup api.github.com: no such host" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "$PKG" ]
  [[ "$output" == *"could not verify $REPO is reachable"* ]]
}

@test "a token with no credentials reaches no verdict" {
  # 401 is not a 404: the repo may well exist and be reachable once the token
  # is fixed, so this must not withdraw the package.
  _write_live_packages "$PKG"
  _stub_gh 'echo "gh: Bad credentials (HTTP 401)" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "$PKG" ]
  [[ "$output" == *"could not verify $REPO is reachable"* ]]
}

@test "an unverifiable repo does not install the package either" {
  _stub_gh 'echo "dial tcp: lookup api.github.com: no such host" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages | length')" = "0" ]
}

@test "a machine with no gh reaches no verdict" {
  _hide_gh

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"could not verify $REPO is reachable"* ]]
}

@test "a package naming no GitHub repo is not gated at all" {
  _write_template '["npm:pi-thing"]'
  _hide_gh

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "npm:pi-thing" ]
}

@test "a package the template no longer declares is left where it is" {
  # Withdrawal is a reachability verdict, not a diff against the template:
  # nothing here can tell a dropped template entry from one the operator
  # installed. Removing a package the workbench once installed is a migration's job.
  _write_live_packages "$PKG"
  _write_template '[]'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.packages[0]')" = "$PKG" ]
}

@test "no packages key is invented when there is nothing to record" {
  _stub_gh 'echo "gh: Not Found (HTTP 404)" >&2; exit 1'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live 'has("packages")')" = "false" ]
}

@test "a second run changes nothing" {
  _stub_gh 'echo "{}"'

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
  [ "$output" = "$PKG" ]
}

@test "the template carries no hardcoded model keys" {
  # Model config comes from ~/.env.local at sync time, not the template.
  # A hardcoded defaultModel or enabledModels would fight the SSOT.
  run jq -e 'has("defaultModel")' "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -ne 0 ]
  run jq -e 'has("enabledModels")' "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -ne 0 ]
}

# ── model injection from ~/.env.local ────────────────────────────────────────────

_seed_env_local() {
  printf '%s\n' "$@" > "$TMPDIR/.env.local"
  export ENV_LOCAL_FILE="$TMPDIR/.env.local"
}

_teardown_env_local() {
  unset ENV_LOCAL_FILE
}

@test "env vars set defaultModel and enabledModels" {
  _seed_env_local \
    'export AI_MODEL=claude-opus-5' \
    "export AI_OPUS_MODEL='claude-opus-5'" \
    "export AI_SONNET_MODEL='claude-sonnet-5'" \
    "export AI_HAIKU_MODEL='claude-haiku-4-5@20251001'"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-5" ]
  [ "$(_live '.enabledModels | length')" = "3" ]
  [ "$(_live '.enabledModels[0]')" = "google-vertex-claude/claude-opus-5" ]
  [ "$(_live '.enabledModels[1]')" = "google-vertex-claude/claude-sonnet-5" ]
  [ "$(_live '.enabledModels[2]')" = "google-vertex-claude/claude-haiku-4-5@20251001" ]
  _teardown_env_local
}

@test "partial env — only AI_MODEL set builds a one-entry enabledModels" {
  _seed_env_local 'export AI_MODEL=claude-opus-5'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-5" ]
  [ "$(_live '.enabledModels | length')" = "1" ]
  [ "$(_live '.enabledModels[0]')" = "google-vertex-claude/claude-opus-5" ]
  _teardown_env_local
}

@test "no AI_MODEL leaves model keys to whatever the template or live file had" {
  _seed_env_local '# nothing set'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  # Template has defaultModel from _write_template
  [ "$(_live '.defaultModel')" = "claude-opus-4-6" ]
  _teardown_env_local
}

@test "env-derived models override stale live values" {
  _seed_env_local 'export AI_MODEL=claude-opus-5'
  _write_live '{"defaultModel": "claude-opus-4-6", "enabledModels": ["google-vertex-claude/claude-opus-4-6"]}'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-5" ]
  [ "$(_live '.enabledModels[0]')" = "google-vertex-claude/claude-opus-5" ]
  _teardown_env_local
}

@test "two tiers naming one model list it once" {
  # Pinning a machine to a single model by pointing several tiers at it is a
  # normal configuration, and enabledModels is a set — the same id twice is
  # not a second model to enable.
  _seed_env_local \
    'export AI_MODEL=claude-opus-5' \
    "export AI_OPUS_MODEL='claude-sonnet-5'" \
    "export AI_SONNET_MODEL='claude-sonnet-5'"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | length')" = "2" ]
  # defaultModel leads, then the one distinct tier value.
  [ "$(_live '.enabledModels[0]')" = "google-vertex-claude/claude-opus-5" ]
  [ "$(_live '.enabledModels[1]')" = "google-vertex-claude/claude-sonnet-5" ]
  _teardown_env_local
}

# ── the model list comes from the registries, not from ai/pi/steps.sh ─────────

# _seed_registry_tree ENTRIES_YAML — a scan root holding one *.env.yml with
# ENTRIES_YAML as its env[], for pointing WORKBENCH_STABLE_DIR at.
_seed_registry_tree() {
  mkdir -p "$TMPDIR/registries"
  cat > "$TMPDIR/registries/models.env.yml" << YAML
meta:
  section: "test models"
  validation: none
env:
$1
YAML
  export WORKBENCH_STABLE_DIR="$TMPDIR/registries"
}

_teardown_registry_tree() {
  unset WORKBENCH_STABLE_DIR
}

@test "a tier added to a registry reaches Pi with no change to the step" {
  # The point of the role field: ai/pi/steps.sh names no model variable, so a
  # fifth tier is a registry edit and nothing else. AI_FAST_MODEL exists in no
  # shipped registry — only in the fixture below.
  _seed_registry_tree '  - var: AI_MODEL
    role: model-default
  - var: AI_FAST_MODEL
    role: model-tier'
  _seed_env_local \
    'export AI_MODEL=claude-opus-5' \
    "export AI_FAST_MODEL='claude-fast-1'"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | length')" = "2" ]
  [ "$(_live '.enabledModels[1]')" = "google-vertex-claude/claude-fast-1" ]
  _teardown_env_local
  _teardown_registry_tree
}

@test "a var carrying no role is not a model" {
  # The guard against keying the list on claude_env instead: ai/lib/vertex.env.yml
  # is flagged for the settings.json mirror, and its GOOGLE_CLOUD_PROJECT would
  # otherwise be offered as something to run a session on.
  _seed_registry_tree '  - var: AI_MODEL
    role: model-default
  - var: GOOGLE_CLOUD_PROJECT
    claude_env: true'
  _seed_env_local \
    'export AI_MODEL=claude-opus-5' \
    'export GOOGLE_CLOUD_PROJECT=some-gcp-project'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | length')" = "1" ]
  [ "$(_live '.enabledModels[0]')" = "google-vertex-claude/claude-opus-5" ]
  _teardown_env_local
  _teardown_registry_tree
}

@test "no model-default declared leaves model keys alone" {
  _seed_registry_tree '  - var: AI_OPUS_MODEL
    role: model-tier'
  _seed_env_local "export AI_OPUS_MODEL='claude-opus-5'"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  # Template's defaultModel survives, and no enabledModels was built from the
  # lone tier — a list with no default in it is not one Pi could select from.
  [ "$(_live '.defaultModel')" = "claude-opus-4-6" ]
  [ "$(_live 'has("enabledModels")')" = "false" ]
  _teardown_env_local
  _teardown_registry_tree
}
