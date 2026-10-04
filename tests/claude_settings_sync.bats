#!/usr/bin/env bats
# Tests for step_claude_settings: the jq merge, additionalDirectories, the manifest, the env mirror, script paths, piped test runs.
setup_file() {
  load 'test_helper'
  load 'claude_settings_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  _sync_settings_into "$BATS_FILE_TMPDIR/home" "$repo_root"
}

setup() {
  load 'test_helper'
  load 'claude_settings_helper'
  common_setup
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
}

teardown() {
  common_teardown
}

# ── sync-settings.jq integrity ───────────────────────────────────────────────

@test "sync-settings.jq file exists" {
  [ -f "$REPO_ROOT/ai/claude/sync-settings.jq" ]
}

@test "sync-settings.jq is valid jq syntax" {
  run jq -n -f "$REPO_ROOT/ai/claude/sync-settings.jq" \
    --argjson t '{"permissions":{"allow":[],"deny":[]}}' \
    --argjson e '{}' --argjson m '{}'
  [ "$status" -eq 0 ]
}

# ── additionalDirectories merge ─────────────────────────────────────────────
#
# _run_sync TEMPLATE EXISTING [MANIFEST] prints the whole envelope, so a test
# asserts on `.settings` for what lands in ~/.claude/settings.json and on
# `.manifest` for what lands in the sidecar.

_run_sync() {
  local manifest="${3-}"
  if [[ -z "$manifest" ]]; then manifest='{}'; fi
  jq -n --argjson t "$1" --argjson e "$2" --argjson m "$manifest" \
    -f "$REPO_ROOT/ai/claude/sync-settings.jq"
}

@test "additionalDirectories: fresh install writes template dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/home/.claude","/home/.config/wb"]},"hooks":{}}' \
    '{}')
  local dirs
  dirs=$(jq -c '.settings.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/home/.claude","/home/.config/wb"]' ]
}

@test "additionalDirectories: tracked in the manifest" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/a","/b"]},"hooks":{}}' \
    '{}')
  local wb_dirs
  wb_dirs=$(jq -c '.manifest.permissions.additionalDirectories' <<< "$result")
  [ "$wb_dirs" = '["/a","/b"]' ]
}

@test "additionalDirectories: user-added dirs are preserved" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/user-custom"]}}' \
    '{"permissions":{"additionalDirectories":["/managed"]}}')
  local dirs
  dirs=$(jq -c '.settings.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/managed","/user-custom"]' ]
}

@test "additionalDirectories: removed managed dir is dropped" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/keep"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/keep","/old-managed"]}}' \
    '{"permissions":{"additionalDirectories":["/keep","/old-managed"]}}')
  local dirs
  dirs=$(jq -c '.settings.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/keep"]' ]
}

@test "additionalDirectories: new managed dir is added alongside user dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed","/new-managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/user-custom"]}}' \
    '{"permissions":{"additionalDirectories":["/managed"]}}')
  local dirs
  dirs=$(jq -c '.settings.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '["/managed","/new-managed","/user-custom"]' ]
}

@test "additionalDirectories: no duplicates on first upgrade from untracked" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/a","/b"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/a"]}}' \
    '{"permissions":{}}')
  local count
  count=$(jq '[.settings.permissions.additionalDirectories[] | select(. == "/a")] | length' <<< "$result")
  [ "$count" -eq 1 ]
}

@test "additionalDirectories: empty template produces empty array" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[]},"hooks":{}}' \
    '{}')
  local dirs
  dirs=$(jq -c '.settings.permissions.additionalDirectories' <<< "$result")
  [ "$dirs" = '[]' ]
}

@test "additionalDirectories: the manifest does not leak user dirs" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[],"additionalDirectories":["/managed"]},"hooks":{}}' \
    '{"permissions":{"additionalDirectories":["/managed","/secret"]}}' \
    '{"permissions":{"additionalDirectories":["/managed"]}}')
  local wb_dirs
  wb_dirs=$(jq -c '.manifest.permissions.additionalDirectories' <<< "$result")
  [ "$wb_dirs" = '["/managed"]' ]
}

# ── manifest lives outside the settings file ─────────────────────────────────
# Claude Code rejects a settings file that declares hook entries under any key
# but `hooks` — and skips the whole file, so every permission and hook in it
# stops applying. The manifest therefore never appears in the settings output.

@test "settings output carries no _workbench key" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":["A"],"deny":[]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard"}]}]}}' \
    '{"_workbench":{"permissions":{"allow":["A"]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard"}]}]}}}')
  local has_key
  has_key=$(jq -c '.settings | has("_workbench")' <<< "$result")
  [ "$has_key" = 'false' ]
}

@test "manifest: a legacy in-file _workbench seeds the first split sync" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":["managed"],"deny":[]},"hooks":{}}' \
    '{"permissions":{"allow":["managed","user"]},"_workbench":{"permissions":{"allow":["managed"]}}}')
  local allow
  allow=$(jq -c '.settings.permissions.allow' <<< "$result")
  [ "$allow" = '["managed","user"]' ]
}

@test "manifest: a sidecar manifest wins over a stale in-file _workbench" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":["managed"],"deny":[]},"hooks":{}}' \
    '{"permissions":{"allow":["managed","stale"]}}' \
    '{"permissions":{"allow":["managed","stale"]}}')
  local allow
  allow=$(jq -c '.settings.permissions.allow' <<< "$result")
  [ "$allow" = '["managed"]' ]
}

@test "manifest: managed hooks are replaced and user hooks preserved" {
  local result
  result=$(_run_sync \
    '{"permissions":{"allow":[],"deny":[]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard-v2"}]}]}}' \
    '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard-v1"}]},{"matcher":"Edit","hooks":[{"type":"command","command":"mine"}]}]}}' \
    '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"guard-v1"}]}]}}')
  local cmds
  cmds=$(jq -c '[.settings.hooks.PreToolUse[].hooks[].command]' <<< "$result")
  [ "$cmds" = '["guard-v2","mine"]' ]
  local tracked
  tracked=$(jq -c '[.manifest.hooks.PreToolUse[].hooks[].command]' <<< "$result")
  [ "$tracked" = '["guard-v2"]' ]
}

# ── the shell half: splitting the envelope across two files ──────────────────
# The tests above drive sync-settings.jq directly. These drive
# step_claude_settings, which is what reads the sidecar off disk, splits the
# envelope, and publishes each half — a swapped selector or a wrong path would
# pass every test above.

# _sync_run FAKE_HOME STATE_DIR — one step_claude_settings against a sandbox.
# In a subshell because constants.sh freezes every path from HOME and the state
# root at source time: a second run needs a fresh process, not a second call.
_sync_run() {
  ( HOME="$1"
    export WORKBENCH_CONFIG_DIR="$1/.config/workbench"
    export WORKBENCH_STATE_DIR="$2"
    export WORKBENCH_CACHE_DIR="$1/.cache/workbench"
    export WORKBENCH_DIR="$REPO_ROOT"
    export WORKBENCH_STABLE_DIR="$REPO_ROOT"
    export NO_COLOR=1
    mkdir -p "$HOME"
    # shellcheck source=/dev/null
    source "$REPO_ROOT/lib/ui.sh"
    # shellcheck source=/dev/null
    source "$REPO_ROOT/ai/claude/steps.sh"
    step_claude_settings >/dev/null )
}

@test "shell: the file-level sync keeps its manifest inside the sandbox" {
  # A sandbox is only as good as the roots it names. setup_file swaps HOME, but
  # roots.sh keeps a root something already set — and the registries.sh it loads
  # first resolves them from the real HOME — so this manifest is the one that
  # would otherwise be written into the operator's own ~/.local/state/workbench.
  [ -f "$BATS_FILE_TMPDIR/home/.local/state/workbench/claude-settings.manifest.json" ]
}

@test "shell: the manifest lands in the state root, not the settings file" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _sync_run "$home" "$state"
  [ -f "$home/.claude/settings.json" ]
  [ -f "$state/claude-settings.manifest.json" ]
  # The manifest holds bookkeeping and nothing else; the settings file holds the
  # template's own keys. Naming both is what catches a swapped selector.
  run jq -ec 'keys' "$state/claude-settings.manifest.json"
  [ "$status" -eq 0 ]
  [ "$output" = '["hooks","permissions"]' ]
  run jq -e 'has("statusLine") and has("hooks")' "$home/.claude/settings.json"
  [ "$status" -eq 0 ]
}

@test "shell: the settings file it writes carries no _workbench key" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _sync_run "$home" "$state"
  run jq -e 'has("_workbench")' "$home/.claude/settings.json"
  [ "$status" -ne 0 ]
}

@test "shell: a second run reproduces both files byte for byte" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _sync_run "$home" "$state"
  cp "$home/.claude/settings.json" "$BATS_TEST_TMPDIR/settings.first"
  cp "$state/claude-settings.manifest.json" "$BATS_TEST_TMPDIR/manifest.first"
  _sync_run "$home" "$state"
  diff "$BATS_TEST_TMPDIR/settings.first" "$home/.claude/settings.json"
  diff "$BATS_TEST_TMPDIR/manifest.first" "$state/claude-settings.manifest.json"
}

@test "shell: a sandbox HOME alone keeps the manifest out of the machine state root" {
  # What setup_file does, in order: load lib/registries.sh — which loads
  # lib/roots.sh on its own — and only then sandbox HOME and sync. While
  # roots.sh read its own first answer as an override, the state root stayed
  # wherever the machine's HOME put it, so the settings file landed in the
  # sandbox and the manifest in the operator's real ~/.local/state/workbench.
  # Every run of this suite overwrote the manifest of the machine running it.
  local machine="$BATS_TEST_TMPDIR/machine" sandbox="$BATS_TEST_TMPDIR/sandbox"
  mkdir -p "$machine" "$sandbox"

  run env -u WORKBENCH_STATE_DIR -u WORKBENCH_CONFIG_DIR -u WORKBENCH_CACHE_DIR \
    -u XDG_STATE_HOME -u XDG_CONFIG_HOME -u XDG_CACHE_HOME \
    HOME="$machine" NO_COLOR=1 WORKBENCH_DIR="$REPO_ROOT" WORKBENCH_STABLE_DIR="$REPO_ROOT" \
    bash -c '
      . "$1/lib/registries.sh"
      HOME="$2"
      . "$1/lib/ui.sh"
      . "$1/ai/claude/steps.sh"
      step_claude_settings >/dev/null
      printf "%s" "$CLAUDE_SETTINGS_MANIFEST"
    ' _ "$REPO_ROOT" "$sandbox"

  [ "$status" -eq 0 ]
  [ "$output" = "$sandbox/.local/state/workbench/claude-settings.manifest.json" ]
  [ -f "$sandbox/.local/state/workbench/claude-settings.manifest.json" ]
  [ -f "$sandbox/.claude/settings.json" ]
  # The machine's own roots stay untouched — the settings file and the manifest
  # have to land under the same HOME or neither describes the other.
  [ ! -e "$machine/.local/state/workbench" ]
}

@test "shell: the sidecar it wrote is what the next run classifies against" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _sync_run "$home" "$state"

  local settings="$home/.claude/settings.json"
  local sidecar="$state/claude-settings.manifest.json"
  # A rule the last sync managed but the template no longer carries, and one the
  # operator added. Only the first is the sidecar's to withdraw.
  local tmp="$BATS_TEST_TMPDIR/edit.json"
  jq '.permissions.allow += ["Bash(retired-managed:*)","Bash(operator-added:*)"]' \
    "$settings" > "$tmp" && mv "$tmp" "$settings"
  jq '.permissions.allow += ["Bash(retired-managed:*)"]' "$sidecar" > "$tmp" && mv "$tmp" "$sidecar"

  _sync_run "$home" "$state"
  run jq -e '.permissions.allow | index("Bash(retired-managed:*)")' "$settings"
  [ "$status" -ne 0 ]
  run jq -e '.permissions.allow | index("Bash(operator-added:*)")' "$settings"
  [ "$status" -eq 0 ]
}

# ── the env block mirrored from ~/.env.local ─────────────────────────────────
# A Claude Code session started outside an interactive shell — the desktop app,
# a launchd job — inherits none of ~/.env.local, so the routing variables have to
# reach it through settings.json. The block is a mirror of that file rather than
# a merge, since settings.json wins over the environment and a stale entry there
# cannot be overridden from a shell.

# _seed_env_local FAKE_HOME LINE... — writes a sandbox ~/.env.local.
_seed_env_local() {
  local home="$1"; shift
  mkdir -p "$home"
  printf '%s\n' "$@" > "$home/.env.local"
}

@test "env mirror: values from ~/.env.local land in the settings env block" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" \
    'export CLAUDE_CODE_USE_VERTEX=1' \
    "export GOOGLE_CLOUD_PROJECT='proj-x'" \
    'export AI_MODEL="claude-opus-5"'
  _sync_run "$home" "$state"

  # Quoting is the shell file's business; the settings file carries the value.
  run jq -r '.env.CLAUDE_CODE_USE_VERTEX' "$home/.claude/settings.json"
  [ "$output" = "1" ]
  # GOOGLE_CLOUD_PROJECT in ~/.env.local is mapped to the name Claude Code reads.
  run jq -r '.env.ANTHROPIC_VERTEX_PROJECT_ID' "$home/.claude/settings.json"
  [ "$output" = "proj-x" ]
  # AI_MODEL in ~/.env.local is mapped to ANTHROPIC_MODEL in the env block.
  run jq -r '.env.ANTHROPIC_MODEL' "$home/.claude/settings.json"
  [ "$output" = "claude-opus-5" ]
}

@test "env mirror: a var absent from ~/.env.local is left out" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" 'export CLAUDE_CODE_USE_VERTEX=1'
  _sync_run "$home" "$state"
  run jq -e '.env | has("CLOUD_ML_REGION")' "$home/.claude/settings.json"
  [ "$status" -ne 0 ]
}

@test "env mirror: a var dropped from ~/.env.local is withdrawn from settings" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" \
    'export CLAUDE_CODE_USE_VERTEX=1' \
    'export CLOUD_ML_REGION=us-east5'
  _sync_run "$home" "$state"
  run jq -r '.env.CLOUD_ML_REGION' "$home/.claude/settings.json"
  [ "$output" = "us-east5" ]

  # Turning Vertex off is a deletion in ~/.env.local and nowhere else.
  _seed_env_local "$home" 'export CLAUDE_CODE_USE_VERTEX=1'
  _sync_run "$home" "$state"
  run jq -e '.env | has("CLOUD_ML_REGION")' "$home/.claude/settings.json"
  [ "$status" -ne 0 ]
}

@test "env mirror: a var no registry declares is left where the operator put it" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" 'export CLAUDE_CODE_USE_VERTEX=1'
  _sync_run "$home" "$state"

  local settings="$home/.claude/settings.json" tmp="$BATS_TEST_TMPDIR/edit.json"
  jq '.env.OPERATOR_OWN_VAR = "keep-me"' "$settings" > "$tmp" && mv "$tmp" "$settings"

  _sync_run "$home" "$state"
  run jq -r '.env.OPERATOR_OWN_VAR' "$settings"
  [ "$output" = "keep-me" ]
  run jq -r '.env.CLAUDE_CODE_USE_VERTEX' "$settings"
  [ "$output" = "1" ]
}

@test "env mirror: a credential in ~/.env.local never reaches settings.json" {
  # ~/.env.local is where the machine's API tokens live and settings.json is
  # written 0644 — only a var a registry volunteered with claude_env crosses.
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" \
    'export CLAUDE_CODE_USE_VERTEX=1' \
    'export JIRA_API_TOKEN=super-secret'
  _sync_run "$home" "$state"
  run grep -c super-secret "$home/.claude/settings.json"
  [ "$status" -ne 0 ]
}

@test "env mirror: no ~/.env.local leaves the settings env block untouched" {
  # A machine with no ~/.env.local has nothing to mirror *from*, which is not the
  # same as having nothing to mirror — emptying the block there would strip a
  # hand-written one on the first sync after the file was renamed or moved.
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  mkdir -p "$home/.claude"
  printf '%s\n' '{"env":{"ANTHROPIC_MODEL":"claude-opus-5"}}' > "$home/.claude/settings.json"
  _sync_run "$home" "$state"
  run jq -r '.env.ANTHROPIC_MODEL' "$home/.claude/settings.json"
  [ "$output" = "claude-opus-5" ]
}

@test "env mirror: a ~/.env.local setting none of them keeps a hand-written block" {
  # The file exists and has content, but nothing in it is the mirror's — the two
  # states that look alike from here are "no values" and "no file", and only the
  # second is a reason to leave the block entirely alone.
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" 'export JIRA_API_TOKEN=super-secret'
  mkdir -p "$home/.claude"
  printf '%s\n' '{"env":{"OPERATOR_OWN_VAR":"keep-me"}}' > "$home/.claude/settings.json"

  _sync_run "$home" "$state"
  run jq -r '.env.OPERATOR_OWN_VAR' "$home/.claude/settings.json"
  [ "$output" = "keep-me" ]
  run jq -r '.env | length' "$home/.claude/settings.json"
  [ "$output" = "1" ]
}

@test "env mirror: a value carrying an = sign survives intact" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" 'export AI_MODEL=claude-opus-5=beta'
  _sync_run "$home" "$state"
  run jq -r '.env.ANTHROPIC_MODEL' "$home/.claude/settings.json"
  [ "$output" = "claude-opus-5=beta" ]
}

@test "env mirror: nothing to mirror leaves no empty env block behind" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" '# nothing this machine routes'
  _sync_run "$home" "$state"
  run jq -e 'has("env")' "$home/.claude/settings.json"
  [ "$status" -ne 0 ]
}

@test "env mirror: an all-empty read keeps the block rather than emptying it" {
  # Not one declared source resolving reads as every variable withdrawn at once,
  # and the sweep would clear the block. It is far likelier a ~/.env.local this
  # run could not read as expected, and withdrawal is the direction that cannot
  # be undone from a shell afterwards — so the ambiguous case keeps what is
  # there. Emptying the block deliberately is still available by dropping the
  # registry's claude_env flag.
  #
  # This is defense in depth rather than cover for a specific bug: a partial read
  # is not caught here, which is why `otto-workbench ai sync` runs migrations
  # before it reaches this code.
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" \
    'export CLAUDE_CODE_USE_VERTEX=1' \
    'export GOOGLE_CLOUD_PROJECT=proj-x'
  _sync_run "$home" "$state"
  run jq -r '.env.ANTHROPIC_VERTEX_PROJECT_ID' "$home/.claude/settings.json"
  [ "$output" = "proj-x" ]

  # A file carrying content, none of it a name any flagged registry declares.
  _seed_env_local "$home" 'export SOMETHING_ELSE=1'
  _sync_run "$home" "$state"
  run jq -r '.env.ANTHROPIC_VERTEX_PROJECT_ID' "$home/.claude/settings.json"
  [ "$output" = "proj-x" ]
  run jq -r '.env.CLAUDE_CODE_USE_VERTEX' "$home/.claude/settings.json"
  [ "$output" = "1" ]
}

@test "env mirror: a second sync reproduces the block byte for byte" {
  local home="$BATS_TEST_TMPDIR/h" state="$BATS_TEST_TMPDIR/s"
  _seed_env_local "$home" \
    'export CLAUDE_CODE_USE_VERTEX=1' \
    'export CLOUD_ML_REGION=global'
  _sync_run "$home" "$state"
  cp "$home/.claude/settings.json" "$BATS_TEST_TMPDIR/first.json"
  _sync_run "$home" "$state"
  diff "$BATS_TEST_TMPDIR/first.json" "$home/.claude/settings.json"
}

# ── script paths referenced from settings ────────────────────────────────────
# Hook and statusline commands name installed scripts by absolute path, and
# nothing resolves those paths at install time. A wrong directory therefore
# fails silently — most of these commands end in `|| true`, and the statusline
# just renders nothing — so the hook looks configured but never runs.

# Every "$HOME/..." path named by the statusline or a hook command. Includes
# data paths (log dirs) as well as scripts — callers filter by prefix.
_referenced_home_paths() {
  jq -r '[.statusLine.command] + [.hooks[][].hooks[].command] | .[]' "$SETTINGS" |
    grep -oE '[$]HOME/[^" ]*' | sort -u
}

@test "settings reference no bin dir other than LOCAL_BIN_DIR" {
  local expected path
  expected=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$expected" ]

  while read -r path; do
    case "$path" in
      "$expected"/*) continue ;;
      */bin/*)
        echo "settings.json references '$path'"
        echo "installed scripts go to $expected (LOCAL_BIN_DIR in lib/constants.sh)"
        return 1
        ;;
    esac
  done < <(_referenced_home_paths)
}

@test "every bin script referenced by settings is one the workbench installs" {
  # Both bin dirs that reach LOCAL_BIN_DIR, not just Claude's. A hook may name a
  # script this harness does not own: the follow-up recorder is called by
  # Claude's PostToolUse entry and by the Pi extension, so it lives in ai/bin —
  # one writer for the ledger rather than a copy per harness. What the check is
  # for is a settings.json naming something nothing installs, which is still
  # caught either way.
  local expected path name missing=()
  expected=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")

  while read -r path; do
    case "$path" in
      "$expected"/*) ;;
      *) continue ;;
    esac
    name="${path##*/}"
    [ -f "$REPO_ROOT/ai/claude/bin/$name" ] || [ -f "$REPO_ROOT/ai/bin/$name" ] \
      || missing+=("$name")
  done < <(_referenced_home_paths)

  [ ${#missing[@]} -eq 0 ] || {
    echo "referenced by settings.json but absent from ai/claude/bin and ai/bin: ${missing[*]}"
    return 1
  }
}

@test "every skill script referenced by settings exists in ai/skills" {
  local path rel missing=()
  while read -r path; do
    case "$path" in
      '$HOME/.claude/skills/'*) ;;
      *) continue ;;
    esac
    rel=${path#'$HOME/.claude/skills/'}
    [ -f "$REPO_ROOT/ai/skills/$rel" ] || missing+=("$rel")
  done < <(_referenced_home_paths)

  [ ${#missing[@]} -eq 0 ] || {
    echo "referenced by settings.json but absent from ai/skills: ${missing[*]}"
    return 1
  }
}

# ── piped test runs ─────────────────────────────────────────────────────────
#
# The rule is testing.md § Reading a Suite's Result, enforced here and by
# ai/pi/extensions/test-pipe-guard for the harness this hook does not run in.

@test "testpipe hook: blocks a suite piped into tail" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ -q | tail -6"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"exit status"* ]]
}

@test "testpipe hook: blocks a suite piped into grep" {
  run _run_guard '{"tool_input":{"command":"bats tests/x.bats | grep -c ok"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: allows the redirect that keeps the status" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ -q > /tmp/out.txt 2>&1; echo $?"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: allows a pipe under set -o pipefail" {
  run _run_guard '{"tool_input":{"command":"set -o pipefail; pytest tests/ | tail -3"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: a || fallback is not a pipe" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ -q || echo failed"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: naming a runner as an argument is not invoking one" {
  run _run_guard '{"tool_input":{"command":"grep pytest notes.md | head"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: piping something that is not a suite is fine" {
  run _run_guard '{"tool_input":{"command":"git log --oneline | head -5"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: blocks a runner that starts a later line" {
  # bash's ^/$ anchor the whole string, not each line, so a runner preceded by
  # an earlier line must still be seen — the lines are joined with `; ` before
  # this rule runs.
  run _run_guard '{"tool_input":{"command":"echo start\npytest tests/ -q | tail -6"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: blocks a runner as a non-leading pipeline stage" {
  run _run_guard '{"tool_input":{"command":"cat file | pytest tests/ | tail -5"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: blocks a filter that is not the segment right after the runner" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ -q | jq . | tail -5"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: a build command piped into a filter is fine" {
  run _run_guard '{"tool_input":{"command":"npm run build | tail -20"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: a go build piped into a filter is fine" {
  run _run_guard '{"tool_input":{"command":"go build ./... | tail -20"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: a cargo build piped into a filter is fine" {
  run _run_guard '{"tool_input":{"command":"cargo build 2>&1 | tail -50"}}'
  [ "$status" -eq 0 ]
}

@test "testpipe hook: blocks go test piped into a filter" {
  run _run_guard '{"tool_input":{"command":"go test ./... | tail -20"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: blocks npm test piped into a filter" {
  run _run_guard '{"tool_input":{"command":"npm test | tail -20"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: blocks npm run test piped into a filter" {
  run _run_guard '{"tool_input":{"command":"npm run test | tail -5"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: a runner reached by path still matches" {
  run _run_guard '{"tool_input":{"command":"bin/local/run-tests | head -20"}}'
  [ "$status" -eq 2 ]
}

@test "testpipe hook: an env prefix is not a way around the rule" {
  run _run_guard '{"tool_input":{"command":"WORKBENCH_X=1 pytest tests/ | wc -l"}}'
  [ "$status" -eq 2 ]
}

# ── Filing an issue during an open self-review ───────────────────────────────
# The guard reads the review the branch is under, so these need a git repo with
# a remote and a branch, plus a state root holding a review file. _run_guard
# above runs against the real cwd, which is this repo on whatever branch the
# suite happens to be on — these cannot use it.

# _guard_in and _review_sandbox live in test_helper.bash — shared with
# pi_extensions.bats, which uses the same layout to compare the Pi guard
# against this one.

@test "guard: blocks gh issue create while the branch review has open findings" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"deferral"* ]]
}

@test "guard: allows gh issue create once every finding is fixed" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" "x")"

  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 0 ]
}

@test "guard: allows gh issue create when the branch has no review" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing")"

  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 0 ]
}

@test "guard: leaves reads and comments alone during an open review" {
  local sandbox cmd
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  for cmd in "gh issue view 1" "gh issue list" "gh pr comment 5 --body x"; do
    run _guard_in "$sandbox" "{\"tool_input\":{\"command\":\"$cmd\"}}"
    [ "$status" -eq 0 ] || {
      echo "blocked a read: $cmd"
      return 1
    }
  done
}

@test "guard: fails open when it cannot tell which review applies" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  # Detached HEAD names no branch, and a repo with no origin names no repo.
  # Either way the guard cannot resolve a review, and a guard that blocked on
  # its own uncertainty would be worse than the mistake it prevents.
  git -C "$sandbox/repo" checkout -q --detach
  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 0 ]

  git -C "$sandbox/repo" checkout -q "isaac/fix/thing"
  git -C "$sandbox/repo" remote remove origin
  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 0 ]
}

@test "guard: the fallback state root matches the one roots.sh derives" {
  # The guard sources nothing, so it spells out REVIEWS_DIR's default. A change
  # to roots.sh that this does not follow makes the guard read an empty
  # directory and fall silent, which is a guard that has stopped working
  # without failing.
  local fallback
  fallback=$(sed -n 's|.*WORKBENCH_STATE_DIR:-\([^}]*\)}.*|\1|p' "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ "$fallback" = '$HOME/.local/state/workbench' ]

  grep -q '"\$HOME/.local/state/workbench"' "$REPO_ROOT/lib/roots.sh"
}
