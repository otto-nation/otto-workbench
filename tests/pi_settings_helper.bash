#!/usr/bin/env bash
# Helpers shared by the pi_settings and pi_models suites: the sandboxed agent
# dir, the managed template, a stubbed gh and pi, and the step runner.

# pi_settings_setup — call from setup() after load 'test_helper'.
pi_settings_setup() {
  common_setup
  AGENT_DIR="$TMPDIR/pi/agent"
  # Read by the suites that load this helper, not by anything in it.
  # shellcheck disable=SC2034
  LIVE="$AGENT_DIR/settings.json"
  TEMPLATE="$TMPDIR/template.json"
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  # Shadow the host `pi` so this suite never calls `pi --list-models` against
  # the developer's install. Tests that cover the check overwrite $BIN/pi;
  # tests that need it absent delete it and narrow PATH.
  cat > "$BIN/pi" << 'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$BIN/pi"
  PATH="$BIN:$PATH"
  ORG="usemaximum"
  REPO="$ORG/pi-extensions"
  PKG="git:github.com/$REPO"
  _write_template "$(jq -nc --arg p "$PKG" '[$p]')"
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

# _stub_gh BODY — a gh on PATH whose whole behaviour is BODY.
_stub_gh() {
  cat > "$BIN/gh" << SCRIPT
#!/usr/bin/env bash
$1
SCRIPT
  chmod +x "$BIN/gh"
  PATH="$BIN:$PATH"
}

# _run_step [STEP] — runs STEP (default step_pi_settings; pi_models.bats passes
# step_pi_models) against the sandbox with the ui helpers stubbed. Runs in its own bash so the step's skip() does not displace bats'.
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
    "$4"
  ' _ "$REPO_ROOT" "$AGENT_DIR" "$TEMPLATE" "${1:-step_pi_settings}"
}

_seed_env_local() {
  printf '%s\n' "$@" > "$TMPDIR/.env.local"
  export ENV_LOCAL_FILE="$TMPDIR/.env.local"
}

_teardown_env_local() {
  unset ENV_LOCAL_FILE
}

# _hide_pi — take pi off PATH and leave every other tool where it was.
# Drops only the PATH entries holding a pi, rather than rebuilding PATH from a
# fixed list: a version manager's shim (mise, asdf) is a symlink that resolves
# its tool by searching PATH, so a shim relinked into a narrowed PATH can no
# longer find the binary it stands in for and fails in place of the tool.
_hide_pi() {
  rm -f "$BIN/pi"
  local dir kept="" entries
  IFS=: read -ra entries <<< "$PATH"
  # Assumes pi never shares a directory with another tool this suite needs
  # (jq, yq, bash): dropping a whole directory for one executable in it would
  # take the others down too. Pi installs through its own installer rather
  # than a version manager (ai/pi/steps.sh step_install_pi), so in practice
  # it lives in a directory of its own.
  for dir in "${entries[@]}"; do
    [[ -x "$dir/pi" ]] || kept+="${kept:+:}$dir"
  done
  PATH="$kept"
  ! command -v pi
}
