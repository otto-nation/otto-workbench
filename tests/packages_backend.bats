#!/usr/bin/env bats
# Tests for packages/setup.sh backend dispatch: brew bundle on macOS with
# Homebrew; entry by entry through install_package everywhere else (Linuxbrew
# when present, mise otherwise). A fixture PACKAGES_DIR supplies the manifests,
# and a uname stub stands in for the OS.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  unset WORKBENCH_INTERACTIVE

  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS" "$TMPDIR/home" "$TMPDIR/state"

  export HOME="$TMPDIR/home"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export PACKAGES_DIR="$TMPDIR/packages"
  export MISE_DATA_DIR="$TMPDIR/mise-data"
  export MISE_LOG="$TMPDIR/mise-args"
  export BREW_LOG="$TMPDIR/brew-args"
  : > "$MISE_LOG"
  : > "$BREW_LOG"

  mkdir -p "$PACKAGES_DIR/tools"
  printf 'brew "jq"  # mise: jq\nbrew "tree"  # linux: system\n' \
    > "$PACKAGES_DIR/Brewfile"
  printf 'brew "schpet/tap/linear"  # mise: github:schpet/linear-cli\nbrew "mas"  # macos-only\nbrew "zsh-syntax-highlighting"  # git: https://github.com/zsh-users/zsh-syntax-highlighting\n' \
    > "$PACKAGES_DIR/tools/tools.Brewfile"

  cat > "$WORKBENCH_STATE_DIR/install.yml" <<'EOF'
components:
  packages:
    stacks:
      - tools/tools
EOF

  # A sysbin without jq, curl, brew, or mise: jq on PATH would make
  # install_package skip the mise install the first test asserts, and curl
  # would let a failed-bootstrap case hit the network.
  SYSBIN="$TMPDIR/sysbin"
  mkdir -p "$SYSBIN"
  local n real
  for n in git grep sed awk cut tr paste wc basename dirname uname cat \
           chmod mkdir rm ls head sort find env mktemp date tee xargs \
           cmp diff true false sleep touch ln cp mv echo printf getconf \
           unexpand expand stat readlink realpath which install; do
    real="$(_runnable_binary "$n" || true)"
    [[ -n "$real" ]] || continue
    ln -sf "$real" "$SYSBIN/$n"
  done
  ln -sf "$BASH" "$SYSBIN/bash"
  real="$(_runnable_binary yq || true)"
  [[ -n "$real" ]] && ln -sf "$real" "$SYSBIN/yq"

  cat > "$STUBS/mise" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == which ]]; then
  echo "$MISE_DATA_DIR/installs/$2/bin/$2"
  exit 0
fi
echo "$*" >> "$MISE_LOG"
[[ -z "${MISE_INSTALL_FAILS:-}" ]] || exit 1
mkdir -p "$MISE_DATA_DIR/shims"
cmd=jq
[[ "$*" == *linear* ]] && cmd=linear
printf '#!/bin/sh\nexit 0\n' > "$MISE_DATA_DIR/shims/$cmd"
chmod +x "$MISE_DATA_DIR/shims/$cmd"
EOF
  chmod +x "$STUBS/mise"
}

# _stub_uname OS — makes `uname -s` answer OS ahead of the real one.
_stub_uname() {
  printf '#!/bin/sh\necho %s\n' "$1" > "$STUBS/uname"
  chmod +x "$STUBS/uname"
}

# _stub_brew — a brew that logs its arguments; `install F` puts F's short name
# on PATH, as a real install would.
_stub_brew() {
  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$BREW_LOG"
[[ "$1 $2" == "info --installed" ]] && echo '{"formulae":[],"casks":[]}'
[[ "$1 $2" == "bundle check" ]] && exit 1
if [[ "$1" == install ]]; then
  bin="$(dirname "$0")/${2##*/}"
  printf '#!/bin/sh\nexit 0\n' > "$bin"
  chmod +x "$bin"
fi
exit 0
EOF
  chmod +x "$STUBS/brew"
}

teardown() {
  common_teardown
}

@test "without brew, saved stacks install through mise" {
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  [[ "$output" == *tree*"package manager"* ]]
  grep -E '– tree +https://.*distro package' <<< "$output"
  [[ "$output" == *mas*macOS* ]]
  # Status line (en-dash + URL), not the install-time "– mas: macOS only".
  grep -E '– mas +https://' <<< "$output"
  grep -E '– zsh-syntax-highlighting +https://.*installed by the zsh component' <<< "$output"
  grep -qx 'use -g jq' "$MISE_LOG"
  grep -qx 'use -g github:schpet/linear-cli' "$MISE_LOG"
  mas_hits=$(grep -c mas "$MISE_LOG" || true)
  [ "$mas_hits" -eq 0 ]
  # tree is a missing system package, so the core stack is not reported installed.
  [[ "$output" != *"core packages installed"* ]]
  [[ "$output" == *"managed via mise"* ]]
}

@test "on macOS with brew present the brew bundle backend is used" {
  _stub_uname Darwin
  _stub_brew
  ln -sf "$(_runnable_binary jq)" "$STUBS/jq"
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  grep -q "^bundle --file=$PACKAGES_DIR/tools/tools.Brewfile" "$BREW_LOG"
  [ ! -s "$MISE_LOG" ]
}

@test "a failed mise install does not print core packages installed" {
  export MISE_INSTALL_FAILS=1
  printf 'brew "jq"  # mise: jq\n' > "$PACKAGES_DIR/Brewfile"
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  [[ "$output" != *"core packages installed"* ]]
}

@test "no brew, no mise, failed bootstrap skips install" {
  run env PATH="$SYSBIN" "$REPO_ROOT/packages/setup.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Neither Homebrew nor mise is available, and mise could not be installed"* ]]
  [ ! -s "$MISE_LOG" ]
}

@test "summary finds mise in LOCAL_BIN_DIR when it is off PATH" {
  mkdir -p "$HOME/.local/bin"
  ln -sf "$STUBS/mise" "$HOME/.local/bin/mise"
  run env PATH="$SYSBIN" HOME="$HOME" bash -c '
    . "$1/lib/ui.sh"
    . "$1/packages/summary.sh"
    print_packages_summary
  ' _ "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"managed via mise"* ]]
  [[ "$output" != *"neither Homebrew nor mise found"* ]]
}

@test "a mise subset install does not print a second success line" {
  export WORKBENCH_INTERACTIVE=1
  # y = confirm core; 1 = tools category; 1 = first package (linear) only.
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< $'y1\n1\n'
  [ "$status" -eq 0 ]
  grep -qx 'use -g github:schpet/linear-cli' "$MISE_LOG"
  linear_ok=$(grep -c 'linear installed' <<< "$output" || true)
  [ "$linear_ok" -eq 1 ]
  extra=$(grep -c 'Installed linear' <<< "$output" || true)
  [ "$extra" -eq 0 ]
}

@test "on Linux with brew present, entries install one by one through Homebrew" {
  _stub_uname Linux
  _stub_brew
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  bundles=$(grep -c bundle "$BREW_LOG" || true)
  [ "$bundles" -eq 0 ]
  grep -qx 'install jq' "$BREW_LOG"
  grep -qx 'install tree' "$BREW_LOG"
  grep -qx 'install schpet/tap/linear' "$BREW_LOG"
  mas_hits=$(grep -c mas "$BREW_LOG" || true)
  [ "$mas_hits" -eq 0 ]
  [ ! -s "$MISE_LOG" ]
  [[ "$output" == *"core packages installed"* ]]
}

@test "without brew, a missing system package does not keep the core prompt open" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/jq"
  chmod +x "$STUBS/jq"
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  [[ "$output" == *"All packages already installed"* ]]
  grep -E '– tree +https://.*distro package' <<< "$output"
  jq_hits=$(grep -c jq "$MISE_LOG" || true)
  [ "$jq_hits" -eq 0 ]
}

@test "an all-macos-only core stack says nothing installable" {
  rm -rf "$PACKAGES_DIR/tools"
  printf 'cask "zed"\nbrew "mas"  # macos-only\n' > "$PACKAGES_DIR/Brewfile"
  printf 'components: {}\n' > "$WORKBENCH_STATE_DIR/install.yml"
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Nothing installable"* ]]
  [[ "$output" != *"All packages already installed"* ]]
}

@test "on Linux with brew, the summary does not judge sync by brew bundle check" {
  _stub_uname Linux
  _stub_brew
  run env PATH="$STUBS:$SYSBIN" bash -c '
    . "$1/lib/ui.sh"
    . "$1/packages/summary.sh"
    print_packages_summary
  ' _ "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed entry by entry through Homebrew"* ]]
  checks=$(grep -c 'bundle check' "$BREW_LOG" || true)
  [ "$checks" -eq 0 ]
}
