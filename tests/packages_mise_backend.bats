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
  printf 'brew "schpet/tap/linear"  # mise: github:schpet/linear-cli\nbrew "mas"  # macos-only\n' \
    > "$PACKAGES_DIR/tools/tools.Brewfile"

  cat > "$WORKBENCH_STATE_DIR/install.yml" <<'EOF'
components:
  packages:
    stacks:
      - tools/tools
EOF

  # yq is not in /usr/bin; state_get_list needs it.
  ln -sf /usr/local/bin/yq "$STUBS/yq"

  # /usr/bin/jq exists on this runner, so a PATH of STUBS:/usr/bin would make
  # install_package skip jq. A sysbin without jq forces the mise install the
  # first test asserts; the brew test puts jq back in STUBS for brew info.
  SYSBIN="$TMPDIR/sysbin"
  mkdir -p "$SYSBIN"
  local n
  for n in git grep sed awk cut tr paste wc basename dirname uname cat \
           chmod mkdir rm ls head sort find env bash mktemp date tee xargs \
           cmp diff true false sleep touch ln cp mv echo printf getconf \
           unexpand expand stat readlink realpath which install; do
    [[ -x "/usr/bin/$n" ]] || continue
    ln -s "/usr/bin/$n" "$SYSBIN/$n"
  done

  cat > "$STUBS/mise" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == which ]]; then
  echo "$MISE_DATA_DIR/installs/$2/bin/$2"
  exit 0
fi
echo "$*" >> "$MISE_LOG"
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
  grep -qx 'use -g jq' "$MISE_LOG"
  grep -qx 'use -g github:schpet/linear-cli' "$MISE_LOG"
  mas_hits=$(grep -c mas "$MISE_LOG" || true)
  [ "$mas_hits" -eq 0 ]
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
  ln -sf /usr/bin/jq "$STUBS/jq"
  run env PATH="$STUBS:$SYSBIN" "$REPO_ROOT/packages/setup.sh" <<< "yy"
  [ "$status" -eq 0 ]
  grep -q "^bundle --file=$PACKAGES_DIR/tools/tools.Brewfile" "$BREW_LOG"
  [ ! -s "$MISE_LOG" ]
}
