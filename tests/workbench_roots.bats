#!/usr/bin/env bats
# Cross-validates the three definitions of the workbench roots (SSOT guard):
#   lib/roots.sh                     — bash, sourced via lib/constants.sh
#   ai/lib/workbench_paths.py        — Python
#   zsh/config.d/aliases/docker.zsh  — inline, because it cannot source
#                                      constants.sh at shell startup
#
# A divergence here is the bug class the roots split exists to prevent: before
# it, lib/constants.sh assigned the state root unconditionally while the Python
# side read WORKBENCH_STATE_DIR, so exporting that variable moved one root and
# not the other.
#
# Also guards the joins under those roots that both languages spell out, where
# the same divergence costs the same thing one level down: a writer and a
# deleter that disagree leave data nothing ever collects.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  unset WORKBENCH_CONFIG_DIR WORKBENCH_STATE_DIR WORKBENCH_CACHE_DIR
  unset XDG_CONFIG_HOME XDG_STATE_HOME XDG_CACHE_HOME
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── Resolvers under test ───────────────────────────────────────────────────

# resolve_shell VAR — the value lib/roots.sh gives VAR in a fresh bash.
resolve_shell() {
  bash -c '. "$1/lib/roots.sh"; printf "%s" "${!2}"' _ "$REPO_ROOT" "$1"
}

# resolve_python FUNC — the value ai/lib/workbench_paths.FUNC() returns.
resolve_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_paths
print(workbench_paths.$1(), end='')
"
}

# resolve_zsh_state — the state root as docker.zsh spells it.
# The line is extracted and evaluated rather than the file sourced: docker.zsh
# unsets the variable once it has used it, so the value cannot be read back.
resolve_zsh_state() {
  local line
  command -v zsh >/dev/null || {
    echo "zsh is required to cross-validate the state root — install it" >&2
    return 1
  }
  line=$(grep -E '^_wb_docker_aliases=' "$REPO_ROOT/zsh/config.d/aliases/docker.zsh")
  [[ -n "$line" ]] || {
    echo "docker.zsh no longer assigns _wb_docker_aliases — update this test" >&2
    return 1
  }
  zsh -fc "$line; print -rn -- \${_wb_docker_aliases%/docker-aliases.zsh}"
}

# ─── Defaults ───────────────────────────────────────────────────────────────

@test "with nothing set, each root falls back to its built-in default" {
  [ "$(resolve_shell WORKBENCH_CONFIG_DIR)" = "$HOME/.config/workbench" ]
  [ "$(resolve_shell WORKBENCH_STATE_DIR)"  = "$HOME/.local/state/workbench" ]
  [ "$(resolve_shell WORKBENCH_CACHE_DIR)"  = "$HOME/.cache/workbench" ]
}

# ─── XDG rung ───────────────────────────────────────────────────────────────

@test "XDG_CONFIG_HOME moves the config root" {
  export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
  [ "$(resolve_shell WORKBENCH_CONFIG_DIR)" = "$TMPDIR/xdg-config/workbench" ]
  [ "$(resolve_python config_dir)" = "$TMPDIR/xdg-config/workbench" ]
}

@test "XDG_CACHE_HOME moves the cache root" {
  export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
  [ "$(resolve_shell WORKBENCH_CACHE_DIR)" = "$TMPDIR/xdg-cache/workbench" ]
  [ "$(resolve_python cache_dir)" = "$TMPDIR/xdg-cache/workbench" ]
}

@test "XDG_STATE_HOME moves the state root" {
  export XDG_STATE_HOME="$TMPDIR/xdg-state"
  [ "$(resolve_shell WORKBENCH_STATE_DIR)" = "$TMPDIR/xdg-state/workbench" ]
  [ "$(resolve_python state_dir)" = "$TMPDIR/xdg-state/workbench" ]
  [ "$(resolve_zsh_state)" = "$TMPDIR/xdg-state/workbench" ]
}

@test "the state root no longer shares the config root's default" {
  # The whole point of the split: generated data that used to sit beside
  # hand-authored config now has a home of its own.
  [ "$(resolve_shell WORKBENCH_STATE_DIR)" != "$(resolve_shell WORKBENCH_CONFIG_DIR)" ]
  [ "$(resolve_python state_dir)" != "$(resolve_python config_dir)" ]
}

# ─── Override rung ──────────────────────────────────────────────────────────

@test "WORKBENCH_<ROOT>_DIR overrides both the XDG rung and the default" {
  export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
  export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/explicit-config"
  export WORKBENCH_STATE_DIR="$TMPDIR/explicit-state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/explicit-cache"

  [ "$(resolve_shell WORKBENCH_CONFIG_DIR)" = "$TMPDIR/explicit-config" ]
  [ "$(resolve_shell WORKBENCH_STATE_DIR)"  = "$TMPDIR/explicit-state" ]
  [ "$(resolve_shell WORKBENCH_CACHE_DIR)"  = "$TMPDIR/explicit-cache" ]
  [ "$(resolve_python config_dir)" = "$TMPDIR/explicit-config" ]
  [ "$(resolve_python state_dir)"  = "$TMPDIR/explicit-state" ]
  [ "$(resolve_python cache_dir)"  = "$TMPDIR/explicit-cache" ]
}

@test "an override that is exported but empty falls through to the default" {
  # `export WORKBENCH_STATE_DIR=` in a shell profile leaves the variable present
  # and empty. Reading that as a real override would resolve the root to `/` and
  # write the workbench's data to the filesystem root.
  export WORKBENCH_CONFIG_DIR="" WORKBENCH_STATE_DIR="" WORKBENCH_CACHE_DIR=""
  [ "$(resolve_shell WORKBENCH_CONFIG_DIR)" = "$HOME/.config/workbench" ]
  [ "$(resolve_shell WORKBENCH_STATE_DIR)"  = "$HOME/.local/state/workbench" ]
  [ "$(resolve_shell WORKBENCH_CACHE_DIR)"  = "$HOME/.cache/workbench" ]
  [ "$(resolve_python config_dir)" = "$HOME/.config/workbench" ]
  [ "$(resolve_python state_dir)"  = "$HOME/.local/state/workbench" ]
  [ "$(resolve_python cache_dir)"  = "$HOME/.cache/workbench" ]
  [ "$(resolve_zsh_state)" = "$HOME/.local/state/workbench" ]
}

@test "sourcing roots.sh does not leave its helper defined" {
  # roots.sh reaches every script that loads lib/ui.sh, so a helper left behind
  # is a name every one of them has to avoid.
  run bash -c '. "$1/lib/roots.sh"; declare -F _wb_root' _ "$REPO_ROOT"
  [ "$status" -ne 0 ]
}

@test "re-sourcing roots.sh keeps an already-resolved root stable" {
  export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
  run bash -c '. "$1/lib/roots.sh"; . "$1/lib/roots.sh"; printf "%s" "$WORKBENCH_CONFIG_DIR"' _ "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/xdg-config/workbench" ]
}

# ─── Cross-validation matrix ────────────────────────────────────────────────

# export_or_unset VAR VALUE — export VAR, or unset it when VALUE is empty.
export_or_unset() {
  if [[ -n "$2" ]]; then
    export "$1=$2"
  else
    unset "$1"
  fi
}

# assert_agree WHAT SHELL_VALUE OTHER_NAME OTHER_VALUE COMBO
assert_agree() {
  if [[ "$2" != "$4" ]]; then
    echo "$1: shell='$2' $3='$4' (combo: $5)" >&2
    return 1
  fi
}

# assert_combo_agrees "STATE|XDG_STATE|XDG_CONFIG|XDG_CACHE" — apply one
# environment and check every resolver against the shell one.
assert_combo_agrees() {
  local combo="$1"
  local -a fields
  # The trailing `|` keeps a combo whose last field is empty from arriving as
  # three fields — `read -ra` drops trailing empties.
  IFS='|' read -ra fields <<< "$combo|"
  # The field count is load-bearing: a stray `|` would shift an XDG_STATE_HOME
  # value into XDG_CONFIG_HOME and the assertions below would still pass.
  if [[ ${#fields[@]} -ne 4 ]]; then
    echo "combo needs 4 |-separated fields, got ${#fields[@]}: '$combo'" >&2
    return 1
  fi
  export_or_unset WORKBENCH_STATE_DIR "${fields[0]}"
  export_or_unset XDG_STATE_HOME "${fields[1]}"
  export_or_unset XDG_CONFIG_HOME "${fields[2]}"
  export_or_unset XDG_CACHE_HOME "${fields[3]}"

  local sh_state
  sh_state="$(resolve_shell WORKBENCH_STATE_DIR)"
  assert_agree "state root" "$sh_state" python "$(resolve_python state_dir)" "$combo"
  assert_agree "state root" "$sh_state" zsh "$(resolve_zsh_state)" "$combo"
  assert_agree "config root" "$(resolve_shell WORKBENCH_CONFIG_DIR)" \
    python "$(resolve_python config_dir)" "$combo"
  assert_agree "cache root" "$(resolve_shell WORKBENCH_CACHE_DIR)" \
    python "$(resolve_python cache_dir)" "$combo"
}

@test "shell, Python, and zsh agree across the set/unset matrix" {
  # The sixteen combinations of WORKBENCH_STATE_DIR x XDG_STATE_HOME x
  # XDG_CONFIG_HOME x XDG_CACHE_HOME, enumerated flat rather than as nested
  # loops so the body stays inside the repo's nesting limit. An empty field
  # means unset. The first two fields are the rungs that must not disagree:
  # the override has to beat XDG_STATE_HOME in all three languages.
  local combo
  for combo in \
    "|||" \
    "$TMPDIR/explicit-state|||" \
    "|$TMPDIR/xdg-state||" \
    "$TMPDIR/explicit-state|$TMPDIR/xdg-state||" \
    "||$TMPDIR/xdg-config|" \
    "$TMPDIR/explicit-state||$TMPDIR/xdg-config|" \
    "|$TMPDIR/xdg-state|$TMPDIR/xdg-config|" \
    "$TMPDIR/explicit-state|$TMPDIR/xdg-state|$TMPDIR/xdg-config|" \
    "|||$TMPDIR/xdg-cache" \
    "$TMPDIR/explicit-state|||$TMPDIR/xdg-cache" \
    "|$TMPDIR/xdg-state||$TMPDIR/xdg-cache" \
    "$TMPDIR/explicit-state|$TMPDIR/xdg-state||$TMPDIR/xdg-cache" \
    "||$TMPDIR/xdg-config|$TMPDIR/xdg-cache" \
    "$TMPDIR/explicit-state||$TMPDIR/xdg-config|$TMPDIR/xdg-cache" \
    "|$TMPDIR/xdg-state|$TMPDIR/xdg-config|$TMPDIR/xdg-cache" \
    "$TMPDIR/explicit-state|$TMPDIR/xdg-state|$TMPDIR/xdg-config|$TMPDIR/xdg-cache"; do
    assert_combo_agrees "$combo"
  done
}

# ─── Joins under the roots ──────────────────────────────────────────────────

# resolve_constants VAR — the value lib/constants.sh gives VAR in a fresh bash.
resolve_constants() {
  bash -c '. "$1/lib/constants.sh"; printf "%s" "${!2}"' _ "$REPO_ROOT" "$1"
}

# resolve_python_retro_consumed — the consumed-reviews file as retro-scan
# spells it: its own CONSUMED_REVIEWS_NAME under the Python state root. Loaded
# through SourceFileLoader because the script carries no .py extension.
resolve_python_retro_consumed() {
  python3 -c "
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader(
    'retro_scan', '$REPO_ROOT/ai/claude/bin/retro-scan')
spec = importlib.util.spec_from_loader('retro_scan', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['retro_scan'] = mod
spec.loader.exec_module(mod)
import workbench_paths
print(workbench_paths.state_dir() / mod.CONSUMED_REVIEWS_NAME, end='')
"
}

@test "bash and Python agree on reviews/ and the consumed-reviews file" {
  [ "$(resolve_constants REVIEWS_DIR)" = "$(resolve_python reviews_dir)" ]
  [ "$(resolve_constants RETRO_CONSUMED_REVIEWS_FILE)" = "$(resolve_python_retro_consumed)" ]
}

@test "both joins ride along when the state root moves" {
  # retro-scan writes the consumed list in Python and retro-complete.sh deletes
  # the directories it names in bash. A root that moves for one and not the
  # other leaves every consumed review on disk with nothing left to collect it.
  export WORKBENCH_STATE_DIR="$TMPDIR/explicit-state"
  local consumed="$TMPDIR/explicit-state/retro-consumed-reviews.txt"

  [ "$(resolve_constants REVIEWS_DIR)" = "$TMPDIR/explicit-state/reviews" ]
  [ "$(resolve_python reviews_dir)" = "$TMPDIR/explicit-state/reviews" ]
  [ "$(resolve_constants RETRO_CONSUMED_REVIEWS_FILE)" = "$consumed" ]
  [ "$(resolve_python_retro_consumed)" = "$consumed" ]
}

@test "bash and Python agree on the installed-scripts bin dir" {
  # Setup symlinks the workbench's scripts into this directory from bash, and
  # MCP discovery defaults to scanning it from Python. A divergence points the
  # server at a directory nothing was ever installed into, and it finds nothing
  # — which is the failure the default was added to end.
  [ "$(resolve_constants LOCAL_BIN_DIR)" = "$HOME/.local/bin" ]
  [ "$(resolve_python local_bin_dir)" = "$HOME/.local/bin" ]
}

# ─── Registry root expansion ────────────────────────────────────────────────

@test "install_check_symlink expands the workbench roots" {
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$WORKBENCH_STATE_DIR"
  ln -sf "$TMPDIR/colima/aliases.zsh" "$WORKBENCH_STATE_DIR/docker-aliases.zsh"

  local registry="$TMPDIR/registry.yml"
  cat > "$registry" <<'YML'
meta:
  install_check: true
  install_check_symlink: "${WORKBENCH_STATE_DIR}/docker-aliases.zsh"
  install_check_symlink_contains: "colima"
tools: []
YML

  source "$REPO_ROOT/lib/registries.sh"
  run registry_passes_install_check "$registry"
  [ "$status" -eq 0 ]
}

@test "install_check_symlink fails when the expanded target does not match" {
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$WORKBENCH_STATE_DIR"
  ln -sf "$TMPDIR/orbstack/aliases.zsh" "$WORKBENCH_STATE_DIR/docker-aliases.zsh"

  local registry="$TMPDIR/registry.yml"
  cat > "$registry" <<'YML'
meta:
  install_check: true
  install_check_symlink: "${WORKBENCH_STATE_DIR}/docker-aliases.zsh"
  install_check_symlink_contains: "colima"
tools: []
YML

  source "$REPO_ROOT/lib/registries.sh"
  run registry_passes_install_check "$registry"
  [ "$status" -ne 0 ]
}

@test "install_check_symlink still expands a leading tilde" {
  mkdir -p "$HOME"
  ln -sf "$TMPDIR/colima/aliases.zsh" "$HOME/legacy-aliases.zsh"

  local registry="$TMPDIR/registry.yml"
  cat > "$registry" <<'YML'
meta:
  install_check: true
  install_check_symlink: "~/legacy-aliases.zsh"
  install_check_symlink_contains: "colima"
tools: []
YML

  source "$REPO_ROOT/lib/registries.sh"
  run registry_passes_install_check "$registry"
  [ "$status" -eq 0 ]
}

@test "docker/registry.yml references the state root rather than a literal path" {
  local value
  value=$(yq '.meta.install_check_symlink' "$REPO_ROOT/docker/registry.yml")
  [[ "$value" == '${WORKBENCH_STATE_DIR}/docker-aliases.zsh' ]]
}
