#!/usr/bin/env bats
# Tests for step_pi_settings in ai/pi/steps.sh — merging the workbench's managed
# keys into Pi's live global settings, with each package gated on whether this
# machine can reach the repo it names.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'pi_settings_helper'
  common_setup
  pi_settings_setup
  # Read by _run_step in pi_settings_helper.bash.
  # shellcheck disable=SC2034
  PI_STEPS="$REPO_ROOT/ai/pi/steps.sh"
}

teardown() {
  common_teardown
}

# _write_live_packages ENTRY... — a live file whose packages hold ENTRY..., each
# a plain source string. Built with jq so the sources stay shell values rather
# than becoming a second spelling of $PKG inside a JSON literal.
_write_live_packages() {
  _write_live "$(jq -nc '$ARGS.positional | {packages: .}' --args "$@")"
}

# _hide_gh — a PATH with no gh on it, which is one of the ways a verdict comes
# back unknown. jq is symlinked in because the step needs it either way, and
# bash because /bin/bash on macOS is 3.2 and has no namerefs — a PATH narrow
# enough to lose gh would otherwise run the step under a shell it predates.
#
# python3 for the same reason as jq: the step reads its models through
# lib/config_cli.py, and the system one may be older than the code it runs.
# _real_bin CMD — the binary CMD actually runs: the first non-shim on PATH, else
# what mise names behind its shim. Linking a mise shim instead would break once
# PATH is narrowed, since a shim with no global version only works by falling
# back to a system binary later on PATH.
_real_bin() {
  local candidate
  while IFS= read -r candidate; do
    [[ "$candidate" == */mise/shims/* ]] || { printf '%s\n' "$candidate"; return 0; }
  done < <(type -ap "$1")
  mise which "$1"
}

_hide_gh() {
  ln -sf "$(_real_bin jq)" "$BIN/jq"
  ln -sf "$(_real_bin python3)" "$BIN/python3"
  ln -sf "$BASH" "$BIN/bash"
  # The base system minus gh. gh is commonly installed in /usr/bin itself, so
  # putting /usr/bin on PATH would leave it found and the case would test a
  # logged-in gh instead of a missing one. One ln over both directories (a
  # per-file loop costs tens of seconds), where a name /bin repeats from
  # /usr/bin on a merged-usr system is refused and the first link kept; then gh
  # goes. $BIN stays first, so its links win over the system ones.
  local sysbin="$TMPDIR/sysbin"
  mkdir -p "$sysbin"
  ln -s /usr/bin/* /bin/* "$sysbin"/ 2>/dev/null || true
  rm -f "$sysbin/gh"
  PATH="$BIN:$sysbin"
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
  # A repo this token cannot see — deleted, renamed, private, or restricted —
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
  run jq -r '.packages[0] | if type == "object" then .source else . end' "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
  [ "$output" = "$PKG" ]
}

@test "the shipped template pins superpowers to a ref" {
  # Tracking main would take 100 commits a quarter unreviewed, and three of the
  # package's skills are overridden by name from ai/skills/ — a changed upstream
  # contract has to be a decision, not a sync. `sync-settings.jq` identifies
  # entries by source with the ref stripped, so the pin survives reconciliation.
  #
  # That the shims record this pin is validate-skills' check, not this one: it
  # runs on every push rather than only when a path this suite names changes.
  run jq -r '.packages[] | (if type == "object" then .source else . end)
    | select(startswith("git:github.com/obra/superpowers"))' \
    "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
  [[ "$output" =~ ^git:github\.com/obra/superpowers@v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

@test "every skill superpowers displaces has a workbench file of the same name" {
  # Name collision is the only containment mechanism that survives the
  # extension, so the shims and the package entry must land together. A sync
  # that installed the package without them would leave using-git-worktrees
  # meaning `git worktree add` into .worktrees/ on this machine.
  run jq -e '.packages | any((if type == "object" then .source else . end)
    | startswith("git:github.com/obra/superpowers"))' \
    "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
  [ -f "$REPO_ROOT/ai/skills/using-git-worktrees/SKILL.md" ]
  [ -f "$REPO_ROOT/ai/skills/finishing-a-development-branch/SKILL.md" ]
  [ -f "$REPO_ROOT/ai/skills/writing-skills/SKILL.md" ]
}

@test "the shipped template filters no superpowers skills" {
  # Every displaced skill is displaced by an ai/skills/ file of the same name —
  # see ai/skills/using-git-worktrees. The package entry filters only upstream's
  # bootstrap extension (see ai/pi/extensions/superpowers-bootstrap); a skills
  # filter beside it would be a second displacement mechanism to keep in step.
  run jq -e '.packages | map(select(type == "object" and has("skills"))) | length == 0' \
    "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
}

@test "the template carries no hardcoded model keys" {
  # Model settings come from config.yml's models.* at sync time. A template
  # defaultModel, enabledModels or defaultProvider would fight that one source.
  run jq -e 'has("defaultModel") or has("enabledModels") or has("defaultProvider")' \
    "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -ne 0 ]
}

# ── model settings from config.yml's models.* ─────────────────────────────────

@test "the tiers and the session set Pi's model keys" {
  _seed_config 'models:' \
    '  deep: anthropic/claude-opus-5-5:high' \
    '  standard: anthropic/claude-sonnet-5-5' \
    '  light: openai/gpt-6-luna' \
    '  session: deep'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel')" = "claude-opus-5-5" ]
  [ "$(_live '.defaultProvider')" = "anthropic" ]
  [ "$(_live '.defaultThinkingLevel')" = "high" ]
  [ "$(_live '.enabledModels | join(",")')" = "anthropic/claude-opus-5-5,anthropic/claude-sonnet-5-5,openai/gpt-6-luna" ]
  [ "$(_live '.modelThinkingLevels | tojson')" = '{"anthropic/claude-opus-5-5":"high"}' ]
}

@test "a bare id is qualified with the provider the settings end up with" {
  # Pi adds a bare pattern once per provider serving it, warning every start,
  # and keys modelThinkingLevels by exact provider/id.
  _write_live '{"defaultProvider": "google-vertex-claude"}'
  _seed_config 'models:' '  deep: claude-opus-5-5:high' '  light: anthropic/claude-haiku-5-5'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | join(",")')" = "google-vertex-claude/claude-opus-5-5,anthropic/claude-haiku-5-5" ]
  [ "$(_live '.modelThinkingLevels | tojson')" = '{"google-vertex-claude/claude-opus-5-5":"high"}' ]
}

@test "with no provider anywhere a bare id stays bare and its level is dropped" {
  _write_template_without_model
  _seed_config 'models:' '  deep: claude-opus-5-5:high'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | join(",")')" = "claude-opus-5-5" ]
  [ "$(_live 'has("modelThinkingLevels")')" = "false" ]
}

@test "two tiers naming one model list it once" {
  # Pinning a machine to a single model by pointing several tiers at it is a
  # normal configuration, and enabledModels is a set.
  _seed_config 'models:' \
    '  deep: anthropic/claude-opus-5-5' \
    '  standard: claude-opus-5-5' \
    '  session: anthropic/claude-opus-5-5'
  _write_live '{"defaultProvider": "anthropic"}'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.enabledModels | join(",")')" = "anthropic/claude-opus-5-5" ]
}

@test "no models in config.yml leaves Pi's own model keys alone" {
  _write_template_without_model
  _write_live '{"defaultModel": "claude-opus-5-5", "enabledModels": ["claude-*"], "defaultProvider": "anthropic"}'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.defaultModel + " " + .defaultProvider')" = "claude-opus-5-5 anthropic" ]
  [ "$(_live '.enabledModels | join(",")')" = "claude-*" ]
}

@test "a level saved in /thinking for another model survives the sync" {
  _write_live '{"modelThinkingLevels": {"openai/gpt-6": "low", "anthropic/claude-opus-5-5": "medium"}}'
  _seed_config 'models:' '  deep: anthropic/claude-opus-5-5:max'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [ "$(_live '.modelThinkingLevels | tojson')" = '{"openai/gpt-6":"low","anthropic/claude-opus-5-5":"max"}' ]
}

@test "a config naming an unset tier warns and leaves the model keys alone" {
  _write_template_without_model
  _write_live '{"defaultModel": "claude-opus-5-5"}'
  _seed_config 'models:' '  session: light'
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Pi's model settings left as they were"* ]]
  [ "$(_live '.defaultModel')" = "claude-opus-5-5" ]
}

@test "a malformed template fails the step rather than writing settings" {
  printf '{ not json' > "$TEMPLATE"
  _stub_gh 'echo "{}"'

  run _run_step
  [ "$status" -ne 0 ]
  [ ! -e "$LIVE" ]
}

# ── A missing default model ─────────────────────────────────────────────────
# Settings left with no defaultModel send pi to its first provider's default,
# a Gemini preview its location may not serve. Nothing fails loudly, so sync
# says so.

# _write_template_without_model — the shipped shape: no model keys at all.
_write_template_without_model() {
  jq -n --arg p "$PKG" '{packages: [$p]}' > "$TEMPLATE"
}

@test "settings left with no defaultModel warn, naming models.session" {
  _write_template_without_model
  _stub_gh 'echo "{}"'
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Pi has no default model — run: otto-workbench config set models.session"* ]]
  [ "$(_live '.defaultModel // "none"')" = "none" ]
}

@test "a defaultModel the operator chose in pi is not reported missing" {
  # /model + Ctrl+S writes defaultModel into the live file; that is a default.
  _write_template_without_model
  _write_live '{"defaultModel": "claude-opus-5-5"}'
  _stub_gh 'echo "{}"'
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pi has no default model"* ]]
}

@test "a session in config.yml is not reported missing" {
  _write_template_without_model
  _seed_config 'models:' '  session: anthropic/claude-opus-5-5'
  _stub_gh 'echo "{}"'
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pi has no default model"* ]]
}

@test "no defaultModel on a machine without pi is silent" {
  _write_template_without_model
  _stub_gh 'echo "{}"'
  _hide_pi
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *"Pi has no default model"* ]]
}
