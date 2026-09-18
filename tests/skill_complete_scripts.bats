#!/usr/bin/env bats
# Tests for the dream and retro close scripts — the trail record they write on
# behalf of a run whose phases nothing else could log.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  sandbox_state_dir
  DREAM_COMPLETE="$REPO_ROOT/ai/skills/dream/dream-complete.sh"
  RETRO_COMPLETE="$REPO_ROOT/ai/skills/retro/retro-complete.sh"
  OTTO_LOG="$REPO_ROOT/ai/bin/otto-log"
  # A HOME of its own, holding the ~/.claude the scripts read and the
  # ~/.local/bin they resolve otto-log through. Both come off HOME inside
  # lib/constants.sh, so this is the only way to point them at a sandbox.
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME/.claude/projects" "$FAKE_HOME/.local/bin"
  ln -sf "$OTTO_LOG" "$FAKE_HOME/.local/bin/otto-log"
}

teardown() {
  common_teardown
}

# The scripts run otto-log, which runs python3 — a mise shim that reads the real
# HOME. Overriding HOME for the sandbox would break it, so the config it wants
# is trusted explicitly rather than left to fail silently under `|| true`.
_run_complete() {
  HOME="$FAKE_HOME" MISE_TRUSTED_CONFIG_PATHS=/ run "$@"
}

# One scan run to hang the rest of a command off, as the real scans open it.
_open_run() {
  local script="$1"
  "$OTTO_LOG" record --script "$script" --action scan --detail "scan"
}

_events_under() {
  local root="$1" script="$2"
  "$OTTO_LOG" query --root "$root" --script "$script" --json | wc -l | tr -d ' '
}

# ── dream ───────────────────────────────────────────────────────────────────

@test "dream close: files itself under the scan it was given" {
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory"
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$(_events_under "$root" dream)" -eq 1 ]]
}

@test "dream close: counts the projects it closed" {
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory" \
           "$FAKE_HOME/.claude/projects/p2/memory"
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  run "$OTTO_LOG" query --root "$root" --script dream --json
  [[ "$output" == *'"projects":2'* ]]
}

@test "dream close: says so when the agent recorded no phases" {
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory"
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" == *"no dream phase records"* ]]
}

@test "dream close: stays quiet when the agent did record its phases" {
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory"
  local root
  root=$(_open_run dream-scan)
  WORKBENCH_TRAIL_ROOT="$root" "$OTTO_LOG" record \
    --script dream --action consolidate --detail "3 added" --data added=3 >/dev/null

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no dream phase records"* ]]
}

@test "dream close: a run with no root is not warned about" {
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory"

  _run_complete bash "$DREAM_COMPLETE"

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no dream phase records"* ]]
}

@test "dream close: still completes when otto-log cannot record" {
  # Every machine until this ships: an installed otto-log with no `record`.
  mkdir -p "$FAKE_HOME/.claude/projects/p1/memory"
  # Removed first: the sandbox entry is a symlink into the repo, and a redirect
  # onto it writes through the link to the checked-out otto-log itself.
  rm -f "$FAKE_HOME/.local/bin/otto-log"
  printf '#!/usr/bin/env bash\nexit 2\n' > "$FAKE_HOME/.local/bin/otto-log"
  chmod +x "$FAKE_HOME/.local/bin/otto-log"
  touch "$FAKE_HOME/.claude/.dream-pending"

  _run_complete bash "$DREAM_COMPLETE" --root aaaaaaaaaaaa

  [[ "$status" -eq 0 ]]
  [[ -f "$FAKE_HOME/.claude/projects/p1/memory/.last-dream" ]]
  [[ ! -f "$FAKE_HOME/.claude/.dream-pending" ]]
}

@test "dream close: --root without a value is refused" {
  _run_complete bash "$DREAM_COMPLETE" --root

  [[ "$status" -eq 2 ]]
  [[ "$output" == *"requires an argument"* ]]
}

# ── retro ───────────────────────────────────────────────────────────────────
#
# The scan ID is retro-complete.sh's first positional argument, and it is also
# the scan's trail root — the deletion it authorises is retro-consume's, and is
# covered in retro_consume.bats. What is tested here is only the trail record
# the close writes under that same ID.

@test "retro close: files itself under the scan that authorised it" {
  local root
  root=$(_open_run retro-scan)

  _run_complete bash "$RETRO_COMPLETE" "$root"

  [[ "$status" -eq 0 ]]
  [[ "$(_events_under "$root" retro)" -eq 1 ]]
}

@test "retro close: says so when the agent recorded no phases" {
  local root
  root=$(_open_run retro-scan)

  _run_complete bash "$RETRO_COMPLETE" "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" == *"no retro phase records"* ]]
}

@test "retro close: stays quiet when the agent did record its phases" {
  local root
  root=$(_open_run retro-scan)
  WORKBENCH_TRAIL_ROOT="$root" "$OTTO_LOG" record \
    --script retro --action propose --detail "2 gaps" --data rule_gaps=2 >/dev/null

  _run_complete bash "$RETRO_COMPLETE" "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no retro phase records"* ]]
}

@test "retro close: a missing scan ID is refused" {
  _run_complete bash "$RETRO_COMPLETE"

  [[ "$status" -eq 2 ]]
  [[ "$output" == *"requires the scan ID"* ]]
}
