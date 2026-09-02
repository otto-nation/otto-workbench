#!/usr/bin/env bats
# Tests for the migration that installs the managed Pi on a machine whose `pi`
# is a Homebrew or npm-global shadow.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS" "$TMPDIR/home/.local/bin"

  export INSTALLER_BODY="$TMPDIR/installer.sh"
  export INSTALLER_RAN="$TMPDIR/installer-ran"

  cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
cat "$INSTALLER_BODY"
EOF
  chmod +x "$STUBS/curl"

  # PATH is pinned to the stub dir so `command -v pi` sees only what a test put
  # there. state_get_list needs yq, so it is linked in rather than the pin being
  # widened to a directory that might hold a real pi.
  ln -s "$(command -v yq)" "$STUBS/yq"

  # The installer's job is to create the launcher; the stub does exactly that.
  cat > "$INSTALLER_BODY" <<'EOF'
touch "$INSTALLER_RAN"
printf '#!/bin/sh\nexit 0\n' > "$HOME/.local/bin/pi"
chmod +x "$HOME/.local/bin/pi"
EOF

  MIGRATION="$REPO_ROOT/ai/pi/migrations/20260902-replace-shadow-pi.sh"
  _selection pi claude
}

# _selection TOOL... — records TOOL... as the machine's saved ai.tools list.
_selection() {
  local dir="$TMPDIR/home/.local/state/workbench"
  mkdir -p "$dir"
  printf 'components:\n  ai:\n    tools:\n' > "$dir/install.yml"
  local tool
  for tool in "$@"; do
    printf -- '      - %s\n' "$tool" >> "$dir/install.yml"
  done
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_run_migration() {
  run bash -c '
    HOME="$3"
    . "$2/lib/ui.sh"
    . "$2/lib/migrations.sh"
    . "$4"
    PATH="$1"
    PI_INSTALL_URL="https://example.invalid/install.sh"
    migration_20260902_replace_shadow_pi
  ' _ "$STUBS:/usr/bin:/bin" "$REPO_ROOT" "$TMPDIR/home" "$MIGRATION"
}

@test "installs the managed pi when the one on PATH is a shadow" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/pi"
  chmod +x "$STUBS/pi"

  _run_migration
  [ "$status" -eq 0 ]
  [ -f "$INSTALLER_RAN" ]
  [ -x "$TMPDIR/home/.local/bin/pi" ]
}

@test "names the shadow so PATH order stays the operator's decision" {
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/pi"
  chmod +x "$STUBS/pi"

  _run_migration
  [[ "$output" == *"$STUBS/pi"* ]]
  [ -x "$STUBS/pi" ]
}

@test "is a no-op once the managed pi exists" {
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/home/.local/bin/pi"
  chmod +x "$TMPDIR/home/.local/bin/pi"

  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "is a no-op when pi is not in the recorded selection" {
  # Migrations run before the component state gate, so this file executes on a
  # machine that chose only claude. Every other migration here edits files; this
  # one would download and run an installer for an agent the operator declined.
  _selection claude
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/pi"
  chmod +x "$STUBS/pi"

  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "is a no-op when no AI tools have been selected at all" {
  _selection
  printf '#!/bin/sh\nexit 0\n' > "$STUBS/pi"
  chmod +x "$STUBS/pi"

  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$INSTALLER_RAN" ]
}

@test "is a no-op on a machine with no pi at all" {
  # Nothing to repair — step_install_pi owns the first install, and a sync that
  # installed a tool the operator never selected would be a surprise.
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$INSTALLER_RAN" ]
}
