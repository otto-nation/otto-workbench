#!/usr/bin/env bats
# Tests for packages/setup.sh backend dispatch: mise when Homebrew is absent,
# brew when it is present. A fixture PACKAGES_DIR supplies the manifests.
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

teardown() {
  common_teardown
}

@test "without brew, saved stacks install through mise" {
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  [[ "$output" == *tree*"package manager"* ]]
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

@test "with brew present the brew backend is used" {
  cat > "$STUBS/brew" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$BREW_LOG"
[[ "$1 $2" == "info --installed" ]] && echo '{"formulae":[],"casks":[]}'
[[ "$1 $2" == "bundle check" ]] && exit 1
exit 0
EOF
  chmod +x "$STUBS/brew"
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
