#!/usr/bin/env bats
# Tests for install_package in lib/packages.sh — how every component installs
# a package named in the manifests: Homebrew where it exists, mise otherwise
# (an unprivileged user on a Linux host), and a warning for an entry with no
# mise spec, such as a distro system package. A fixture manifest supplies the
# entries; rtk stands in for any formula-plus-mise tool, since the branches do
# not depend on which one it is. The callers' own cases are at the end.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"

  export PACKAGES_DIR="$TMPDIR/packages"
  mkdir -p "$PACKAGES_DIR"
  printf 'brew "rtk"  # mise: rtk\nbrew "otto-test-syspkg"  # linux: system\nbrew "mas"  # macos-only\nbrew "pinned"  # mise: pinned@9\nbrew "zsh-foo"  # git: https://example.com/zsh-foo\n' > "$PACKAGES_DIR/Brewfile"

  # Exported, because the stubs read them from the environment.
  export BREW_LOG="$TMPDIR/brew-args"
  export MISE_LOG="$TMPDIR/mise-args"
  export MISE_DATA_DIR="$TMPDIR/mise-data"

  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$BREW_LOG"
[[ -z "${BREW_INSTALL_FAILS:-}" ]] || exit 1
# Models a formula that links its binary onto PATH, next to brew itself.
[[ -n "${BREW_LEAVES_NO_BINARY:-}" ]] && exit 0
printf '#!/bin/sh\nexit 0\n' > "$(dirname "$0")/rtk"
chmod +x "$(dirname "$0")/rtk"
EOF
  chmod +x "$STUBS/brew"

  # Models `mise use -g TOOL`: the tool's shim appears under the data dir's
  # shims, which is not on PATH — exactly the state a fresh mise install leaves.
  cat > "$STUBS/mise" <<'EOF'
#!/usr/bin/env bash
# `mise which TOOL` names the binary behind a shim; it fails for a shim with no
# active version, which MISE_WHICH_FAILS models. Not logged: the log records
# installs.
if [[ "$1" == which ]]; then
  [[ -z "${MISE_WHICH_FAILS:-}" ]] || exit 1
  echo "$MISE_DATA_DIR/installs/$2/bin/$2"
  exit 0
fi
echo "$*" >> "$MISE_LOG"
[[ -z "${MISE_INSTALL_FAILS:-}" ]] || exit 1
mkdir -p "$MISE_DATA_DIR/shims"
printf '#!/bin/sh\nexit 0\n' > "$MISE_DATA_DIR/shims/rtk"
chmod +x "$MISE_DATA_DIR/shims/rtk"
EOF
  chmod +x "$STUBS/mise"
}

teardown() {
  common_teardown
}

# Runs install_package against a PATH of only the chosen stubs and the base
# system, then reports where rtk resolves in the same shell — the property the
# shim PATH update exists for. PATH is narrowed after lib/ui.sh loads, since
# output.sh needs a modern bash to source at all.
# Extra arguments after PATH are passed to install_package (default: rtk).
#
# HOME is the test's own, so LOCAL_BIN_DIR (where mise's installer lands) is
# too. run_remote_installer is always replaced: by INSTALLER_STUB when a test
# sets one, and otherwise by a refusal, so no case can download mise for real.
_run_install() {
  local path="$1"
  shift
  local -a pkg_args=()
  if [[ $# -gt 0 ]]; then
    pkg_args=("$@")
  else
    pkg_args=(rtk)
  fi
  bash -c '
    HOME="$3"
    . "$2/lib/ui.sh"
    [[ -z "${HOMEBREW_BIN_DIR_LINUX_OVERRIDE:-}" ]] || HOMEBREW_BIN_DIR_LINUX="$HOMEBREW_BIN_DIR_LINUX_OVERRIDE"
    run_remote_installer() {
      [[ -n "${INSTALLER_STUB:-}" ]] || return 1
      echo "$1" >> "$INSTALLER_LOG"
      "$INSTALLER_STUB"
    }
    PATH="$1"
    shift 3
    install_package "$@"
    status=$?
    echo "RESOLVED=$(command -v rtk || true)"
    exit "$status"
  ' _ "$path" "$REPO_ROOT" "$TMPDIR/home" "${pkg_args[@]}"
}

# _path_with STUB... — a PATH holding only the named stubs and the base system.
# /opt/homebrew/bin is never on it, so the developer's real brew is unreachable.
_path_with() {
  local dir="$TMPDIR/path-$*" name
  dir="${dir// /-}"
  mkdir -p "$dir"
  for name in "$@"; do ln -sf "$STUBS/$name" "$dir/$name"; done
  echo "$dir:/usr/bin:/bin"
}

@test "a tool already in PATH is not reinstalled" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/rtk"
  chmod +x "$STUBS/rtk"

  run _run_install "$STUBS:/usr/bin:/bin"
  [ "$status" -eq 0 ]
  [[ "$output" == *"rtk already installed"* ]]
  [ ! -e "$BREW_LOG" ]
  [ ! -e "$MISE_LOG" ]
}

@test "Homebrew is used when it is available, even with mise present" {
  run _run_install "$(_path_with brew mise)"
  [ "$status" -eq 0 ]
  [[ "$(cat "$BREW_LOG")" == "install rtk" ]]
  [ ! -e "$MISE_LOG" ]
}

@test "without Homebrew, mise installs the tool globally" {
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$(cat "$MISE_LOG")" == "use -g rtk" ]]
  [[ "$output" == *"rtk installed"* ]]
}

@test "a mise install resolves in the same shell through the shims dir" {
  # The steps after the install (the rtk hook, the worktrunk plugin) look the
  # tool up with command -v. Without the shims on PATH they would skip it until
  # the next login.
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"RESOLVED=$MISE_DATA_DIR/shims/rtk"* ]]
}

@test "a mise install that leaves no shim warns and fails" {
  # mise exits 0 but writes nothing to the shims dir: the later command -v
  # would fail with no hint why.
  cat > "$STUBS/mise" <<'EOF2'
#!/usr/bin/env bash
echo "$*" >> "$MISE_LOG"
EOF2
  chmod +x "$STUBS/mise"
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not on PATH"* ]]
  [[ "$output" != *"rtk installed"* ]]
}

@test "a brew install that leaves no binary on PATH warns and fails" {
  export BREW_LEAVES_NO_BINARY=1
  run _run_install "$(_path_with brew)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not on PATH"* ]]
  [[ "$output" != *"rtk installed"* ]]
}

@test "with neither installer, mise is bootstrapped and then installs the tool" {
  # The installer stub models mise.run: it writes a mise into ~/.local/bin,
  # which is not on PATH yet — the bootstrap has to put it there itself.
  export INSTALLER_LOG="$TMPDIR/installer-log"
  export INSTALLER_STUB="$TMPDIR/fake-mise-installer"
  cat > "$INSTALLER_STUB" <<EOF2
#!/usr/bin/env bash
mkdir -p "$TMPDIR/home/.local/bin"
ln -sf "$STUBS/mise" "$TMPDIR/home/.local/bin/mise"
EOF2
  chmod +x "$INSTALLER_STUB"
  run _run_install "$(_path_with)"
  [ "$status" -eq 0 ]
  [ "$(cat "$INSTALLER_LOG")" = "https://mise.run" ]
  [ "$(cat "$MISE_LOG")" = "use -g rtk" ]
  [[ "$output" == *"mise installed"* ]]
  [[ "$output" == *"rtk installed"* ]]
}

@test "the bootstrap does not pull an off-PATH Homebrew in halfway" {
  # The CI runner's shape: Homebrew installed under its Linux prefix but not on
  # PATH. A bootstrap that adds every tool dir would find it and switch to a
  # real brew install mid-decision.
  export INSTALLER_LOG="$TMPDIR/installer-log"
  export INSTALLER_STUB="$TMPDIR/fake-mise-installer"
  cat > "$INSTALLER_STUB" <<EOF2
#!/usr/bin/env bash
mkdir -p "$TMPDIR/home/.local/bin"
ln -sf "$STUBS/mise" "$TMPDIR/home/.local/bin/mise"
EOF2
  chmod +x "$INSTALLER_STUB"
  mkdir -p "$TMPDIR/linuxbrew"
  printf '#!/bin/sh\necho "BREW $*" >> "%s"\n' "$BREW_LOG" > "$TMPDIR/linuxbrew/brew"
  chmod +x "$TMPDIR/linuxbrew/brew"
  HOMEBREW_BIN_DIR_LINUX_OVERRIDE="$TMPDIR/linuxbrew" run _run_install "$(_path_with)"
  [ "$status" -eq 0 ]
  [ ! -e "$BREW_LOG" ]
  [ "$(cat "$MISE_LOG")" = "use -g rtk" ]
}

@test "the mise component installs from the same URL constant as the bootstrap" {
  grep -q 'curl -fsSL "\$MISE_INSTALL_URL" | sh' "$REPO_ROOT/mise/steps.sh"
}

@test "with neither installer and a failed bootstrap, the warning names both commands" {
  run _run_install "$(_path_with)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"brew install rtk"* ]]
  [[ "$output" == *"mise use -g rtk"* ]]
  [ ! -e "$BREW_LOG" ]
  [ ! -e "$MISE_LOG" ]
}

@test "a bootstrap that leaves no mise on PATH is reported, not mistaken for an install" {
  export INSTALLER_LOG="$TMPDIR/installer-log"
  export INSTALLER_STUB="$TMPDIR/no-op-installer"
  printf '#!/bin/sh\nexit 0\n' > "$INSTALLER_STUB"
  chmod +x "$INSTALLER_STUB"
  run _run_install "$(_path_with)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"mise's installer ran but left no mise on PATH"* ]]
  [[ "$output" != *"rtk installed"* ]]
}

@test "brew present means mise is never bootstrapped" {
  export INSTALLER_LOG="$TMPDIR/installer-log"
  export INSTALLER_STUB="$TMPDIR/should-not-run"
  run _run_install "$(_path_with brew)"
  [ "$status" -eq 0 ]
  [ ! -e "$INSTALLER_LOG" ]
}

@test "a failed brew install is not reported as an install" {
  export BREW_INSTALL_FAILS=1
  run _run_install "$(_path_with brew)"
  [ "$status" -eq 1 ]
  [[ "$output" != *"rtk installed"* ]]
  [[ "$output" == *"brew install rtk"* ]]
}

@test "a failed mise install is not reported as an install" {
  export MISE_INSTALL_FAILS=1
  run _run_install "$(_path_with mise)"
  [ "$status" -eq 1 ]
  [[ "$output" != *"rtk installed"* ]]
  [[ "$output" == *"mise use -g rtk"* ]]
}

@test "a name in no manifest fails and names the manifests searched" {
  run _run_install "$(_path_with brew)" nope
  [ "$status" -eq 1 ]
  [[ "$output" == *nope* && "$output" == *"$PACKAGES_DIR"* ]]
}

@test "--version pins the mise spec" {
  run -- _run_install "$(_path_with mise)" rtk --version 1.2.3
  [ "$status" -eq 0 ]
  grep -qx 'use -g rtk@1.2.3' "$MISE_LOG"
}

@test "an unknown or extra argument is a usage error, not an unpinned install" {
  run -- _run_install "$(_path_with mise)" rtk --verison 1.2.3
  [ "$status" -eq 2 ]
  run -- _run_install "$(_path_with mise)" rtk 1.2.3
  [ "$status" -eq 2 ]
  run -- _run_install "$(_path_with mise)" rtk --version 1.2.3 extra
  [ "$status" -eq 2 ]
  run -- _run_install "$(_path_with mise)" --version 1.2.3 rtk
  [ "$status" -eq 2 ]
  [ ! -s "$MISE_LOG" ]
}

@test "a system package on the mise path is reported, not installed" {
  run _run_install "$(_path_with mise)" otto-test-syspkg
  [ "$status" -eq 1 ]
  [[ "$output" == *"package manager"* ]]
  [ ! -s "$MISE_LOG" ]
}

@test "a system package with neither installer is reported without bootstrapping mise" {
  export INSTALLER_LOG="$TMPDIR/installer-log"
  export INSTALLER_STUB="$TMPDIR/fake-mise-installer"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$INSTALLER_STUB"
  chmod +x "$INSTALLER_STUB"
  run _run_install "$(_path_with)" otto-test-syspkg
  [ "$status" -eq 1 ]
  [[ "$output" == *"package manager"* ]]
  [ ! -e "$INSTALLER_LOG" ]
}

@test "--version without a value is a usage error" {
  run -- _run_install "$(_path_with)" rtk --version
  [ "$status" -eq 2 ]
}

@test "--version on a spec that already contains @ is a usage error" {
  run -- _run_install "$(_path_with mise)" pinned --version 1.2.3
  [ "$status" -eq 2 ]
  [[ "$output" == *"already pins a version"* ]]
  [ ! -s "$MISE_LOG" ]
}

@test "a git-disposition package without Homebrew names the zsh component" {
  run _run_install "$(_path_with mise)" zsh-foo
  [ "$status" -eq 1 ]
  [[ "$output" == *"installed by the zsh component"* ]]
  [[ "$output" != *"Linux"* ]]
}

@test "a macos-only package without Homebrew does not say Linux" {
  run _run_install "$(_path_with mise)" mas
  [ "$status" -eq 1 ]
  [[ "$output" == *"no install without Homebrew"* ]]
  [[ "$output" != *"Linux"* ]]
}

# ─── A shim with no active version ───────────────────────────────────────────
# A tool one project pins in its own mise config is installed and shimmed but has
# no global version, so its shim resolves on PATH and fails everywhere else.

# _plant_shim — an rtk shim under the mise shims dir, as that state leaves it.
_plant_shim() {
  mkdir -p "$MISE_DATA_DIR/shims"
  printf '#!/bin/sh\nexit 1\n' > "$MISE_DATA_DIR/shims/rtk"
  chmod +x "$MISE_DATA_DIR/shims/rtk"
}

@test "a mise shim with no active version is installed globally rather than skipped" {
  _plant_shim
  export MISE_WHICH_FAILS=1
  run _run_install "$MISE_DATA_DIR/shims:$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$output" != *"rtk already installed"* ]]
  [ "$(cat "$MISE_LOG")" = "use -g rtk" ]
}

@test "a versionless shim with a system binary behind it counts as installed" {
  # mise falls back to a non-shim later on PATH, so the shim works: a distro
  # jq behind a project-pinned one is the everyday shape of this.
  _plant_shim
  export MISE_WHICH_FAILS=1
  mkdir -p "$TMPDIR/sysbin"
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/sysbin/rtk"
  chmod +x "$TMPDIR/sysbin/rtk"
  run _run_install "$MISE_DATA_DIR/shims:$TMPDIR/sysbin:$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"rtk already installed"* ]]
  [ ! -e "$MISE_LOG" ]
}

@test "a mise shim that resolves to an active version is not reinstalled" {
  _plant_shim
  run _run_install "$MISE_DATA_DIR/shims:$(_path_with mise)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"rtk already installed"* ]]
  [ ! -e "$MISE_LOG" ]
}

# ─── Callers ─────────────────────────────────────────────────────────────────
# The callers pass their own formula and mise tool names; a swapped or misspelt
# name installs nothing on the machines that need the fallback, so the wiring
# is pinned here with the helper stubbed to record its arguments.

# _run_caller STEPS_FILE FN — runs FN from STEPS_FILE with the ui helpers and
# install_package stubbed. `wt` and `rtk` are absent from PATH so each step
# reaches its install.
_run_caller() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    install_package() { echo "HELPER $*"; return "${HELPER_STATUS:-1}"; }
    _cmd_runnable() { command -v "$1" >/dev/null 2>&1; }
    WORKBENCH_DIR="$3"
    . "$1"
    PATH="${CALLER_PATH:-/usr/bin:/bin}"
    "$2"
  ' _ "$1" "$2" "$REPO_ROOT"
}

@test "step_install_rtk installs rtk through install_package" {
  run _run_caller "$REPO_ROOT/ai/rtk/steps.sh" step_install_rtk
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER rtk"* ]]
}

@test "the worktrunk plugin step installs worktrunk through the helper first" {
  run _run_caller "$REPO_ROOT/ai/claude/steps.sh" step_claude_worktrunk_plugin
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER worktrunk"* ]]
}

@test "the worktrunk plugin step goes on to the plugin list once worktrunk installs" {
  cat > "$STUBS/wt" <<'EOF2'
#!/usr/bin/env bash
echo "WT $*"
[[ "$*" == "config plugins list" ]] && echo "claude"
exit 0
EOF2
  chmod +x "$STUBS/wt"
  HELPER_STATUS=0 CALLER_PATH="$STUBS:/usr/bin:/bin" run _run_caller "$REPO_ROOT/ai/claude/steps.sh" step_claude_worktrunk_plugin
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrunk Claude plugin already installed"* ]]
  [[ "$output" != *"skipped"* ]]
}

@test "the git hook-tools step installs gitleaks, and nothing a workbench developer alone needs" {
  run _run_caller "$REPO_ROOT/git/steps.sh" step_install_hook_tools
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER gitleaks"* ]]
  [[ "$output" != *"HELPER bats"* ]]
  [[ "$output" != *"HELPER shellcheck"* ]]
  [[ "$output" != *"HELPER pytest"* ]]
}

@test "the git component installs worktrunk through the helper" {
  run _run_caller "$REPO_ROOT/git/steps.sh" step_install_worktrunk
  [ "$status" -eq 0 ]
  [[ "$output" == *"HELPER worktrunk"* ]]
}

@test "install_git and sync_git install worktrunk before configuring it" {
  local fn body
  for fn in install_git sync_git; do
    body="$(sed -n "/^$fn()/,/^}/p" "$REPO_ROOT/git/steps.sh")"
    [[ "$body" == *step_install_worktrunk*step_worktrunk_config* ]]
  done
}
