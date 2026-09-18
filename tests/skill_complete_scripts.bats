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
  mkdir -p "$FAKE_HOME/.claude/projects" "$FAKE_HOME/.local/bin" "$WORKBENCH_STATE_DIR"
  ln -sf "$OTTO_LOG" "$FAKE_HOME/.local/bin/otto-log"
}

teardown() {
  common_teardown
}

# The scripts run otto-log, which runs python3 — a mise shim that reads the real
# HOME. Overriding HOME for the sandbox would break it, so the config it wants
# is trusted explicitly rather than left to fail silently under `|| true`.
#
# The trusted path is captured before HOME is reassigned, not read out of the
# same assignment list: bash expands the right-hand sides against the current
# environment, so `MISE_TRUSTED_CONFIG_PATHS="$HOME"` beside `HOME="$FAKE_HOME"`
# happens to mean the real HOME today and would silently mean the sandbox the
# day someone splits the line.
_run_complete() {
  local real_home="$HOME"
  HOME="$FAKE_HOME" MISE_TRUSTED_CONFIG_PATHS="$real_home" run "$@"
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

# _memory_project NAME — a registered repo with a memory directory, printed.
#
# The close sweeps the project registry and encodes each repo's path into the
# directory its memory lives in, so a bare directory under `.claude/projects`
# named for nothing is swept by nothing. The registry line carries its identity
# field, which is what a machine the sync has already run holds and is what
# keeps the fixture from depending on git discovery under $TMPDIR.
_memory_project() {
  local repo="$TMPDIR/repos/$1" mem
  mkdir -p "$repo"
  printf '%s\t%s\n' "$repo" "$repo/.git" >> "$WORKBENCH_STATE_DIR/projects.registry"
  mem="$FAKE_HOME/.claude/projects/$(printf '%s' "$repo" | tr -c 'A-Za-z0-9' '-')/memory"
  mkdir -p "$mem"
  printf '%s' "$mem"
}

# ── dream ───────────────────────────────────────────────────────────────────

@test "dream close: files itself under the scan it was given" {
  _memory_project p1
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$(_events_under "$root" dream)" -eq 1 ]]
}

@test "dream close: counts the projects it closed" {
  _memory_project p1
  _memory_project p2
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  run "$OTTO_LOG" query --root "$root" --script dream --json
  [[ "$output" == *'"projects":2}'* ]]
}

@test "dream close: says so when the agent recorded no phases" {
  _memory_project p1
  local root
  root=$(_open_run dream-scan)

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" == *"no dream phase records"* ]]
}

@test "dream close: stays quiet when the agent did record its phases" {
  _memory_project p1
  local root
  root=$(_open_run dream-scan)
  WORKBENCH_TRAIL_ROOT="$root" "$OTTO_LOG" record \
    --script dream --action consolidate --detail "3 added" --data added=3 >/dev/null

  _run_complete bash "$DREAM_COMPLETE" --root "$root"

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no dream phase records"* ]]
}

@test "dream close: a run with no root is not warned about" {
  _memory_project p1

  _run_complete bash "$DREAM_COMPLETE"

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no dream phase records"* ]]
}

@test "dream close: a query that fails is not reported as an empty run" {
  # An otto-log that errors on every subcommand. The note is about a run whose
  # phases went unrecorded; a query that could not answer knows nothing about
  # that either way, and saying so anyway sends the reader after a run that is
  # on the trail.
  _memory_project p1
  rm -f "$FAKE_HOME/.local/bin/otto-log"
  printf '#!/usr/bin/env bash\nexit 1\n' > "$FAKE_HOME/.local/bin/otto-log"
  chmod +x "$FAKE_HOME/.local/bin/otto-log"

  _run_complete bash "$DREAM_COMPLETE" --root aaaaaaaaaaaa

  [[ "$status" -eq 0 ]]
  [[ "$output" != *"no dream phase records"* ]]
}

@test "dream close: still completes when otto-log cannot record" {
  # Every machine until this ships: an installed otto-log with no `record`.
  local memory
  memory=$(_memory_project p1)
  # Removed first: the sandbox entry is a symlink into the repo, and a redirect
  # onto it writes through the link to the checked-out otto-log itself.
  rm -f "$FAKE_HOME/.local/bin/otto-log"
  printf '#!/usr/bin/env bash\nexit 2\n' > "$FAKE_HOME/.local/bin/otto-log"
  chmod +x "$FAKE_HOME/.local/bin/otto-log"

  _run_complete bash "$DREAM_COMPLETE" --root aaaaaaaaaaaa

  [[ "$status" -eq 0 ]]
  [[ -f "$memory/.last-dream" ]]
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
