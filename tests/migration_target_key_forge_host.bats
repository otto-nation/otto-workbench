#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260928-target-key-forge-host.sh — renames run
# targets whose key changed when the declared forge host entered it.
#
# The directory this moves holds a thread ledger no API call reproduces, so the
# cases that matter most are the ones where it must NOT move: an undeclared
# repo, a destination that already exists, and a target another run is holding.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260928-target-key-forge-host.sh"
  STATE="$TMPDIR/state"
  mkdir -p "$STATE/pr"
}

teardown() {
  common_teardown
}

# A checkout the migration can key: an origin to canonicalise, a commit so the
# branch resolves, and optionally a declared forge instance.
_init_repo() {
  local path="$1" origin="$2" declared="${3:-}"
  mkdir -p "$path"
  git -C "$path" init -q -b main
  git -C "$path" remote add origin "$origin"
  git -C "$path" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  [[ -z "$declared" ]] || printf 'github:\n  host: %s\n' "$declared" > "$path/.workbench.yml"
}

# The key `pr.target` derives for a checkout right now.
_key_for_repo() {
  WORKBENCH_STATE_DIR="$STATE" python3 - "$REPO_ROOT" "$1" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target
ident = pr_target.repo_identity_from_origin(sys.argv[2])
print(ident.key if ident else "")
PY
}

# A target directory as a real run leaves it: a state.json naming the checkout
# it was resolved from, and a ledger standing in for what must survive a move.
_seed_target() {
  local name="$1" worktree="$2" repo_key="${3:-}"
  local dir="$STATE/pr/$name"
  mkdir -p "$dir/pr-comments"
  python3 - "$dir" "$worktree" "$repo_key" <<'PY'
import json, sys
from pathlib import Path
d, wt, key = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
(d / "state.json").write_text(json.dumps({
    "identity": {"repo": "acme/widget", "branch": "main", "pr_number": None,
                 "head_sha": "abc1234", "worktree_root": wt,
                 **({"repo_key": key} if key else {})},
}))
PY
  printf '{"decided":"by hand"}\n' > "$dir/pr-comments/state.json"
  printf '%s\n' "$dir"
}

_run_migration() {
  WORKBENCH_STATE_DIR="$STATE" run bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    MIGRATION_NOOP=3
    MIGRATION_DEFERRED=4
    export WORKBENCH_STATE_DIR
    . "$1"
    migration_20260928_target_key_forge_host
  ' _ "$MIGRATION" "$REPO_ROOT"
}

@test "a declared enterprise repo is rekeyed and keeps its ledger" {
  local repo="$TMPDIR/repo"
  _init_repo "$repo" "git@ghe.acme.com:acme/widget.git" "ghe.acme.com"
  # The key this target had before the host entered it: the same repo with
  # nothing declared.
  local repo_pub="$TMPDIR/pub"
  _init_repo "$repo_pub" "git@ghe.acme.com:acme/widget.git"
  local old new
  old="$(_key_for_repo "$repo_pub")"
  new="$(_key_for_repo "$repo")"
  [ "$old" != "$new" ]
  _seed_target "${old}-main" "$repo" "$old"

  _run_migration

  [ "$status" -eq 0 ]
  [[ "$output" == *"Rekeyed 1 run target"* ]]
  [ -d "$STATE/pr/${new}-main" ]
  [ ! -d "$STATE/pr/${old}-main" ]
  # The ledger is the thing that cannot be rebuilt — it has to arrive intact.
  run cat "$STATE/pr/${new}-main/pr-comments/state.json"
  [[ "$output" == *"by hand"* ]]
}

@test "an undeclared repo is left where it is" {
  local repo="$TMPDIR/repo"
  _init_repo "$repo" "git@github.com:acme/widget.git"
  local key; key="$(_key_for_repo "$repo")"
  _seed_target "${key}-main" "$repo" "$key"

  _run_migration

  [ "$status" -eq 3 ]
  [ -d "$STATE/pr/${key}-main" ]
}

@test "a target whose destination already exists is skipped, not merged" {
  # Two ledgers for one target cannot be reconciled by a script: each records
  # decisions the other does not have, and picking one discards the rest.
  local repo="$TMPDIR/repo" repo_pub="$TMPDIR/pub"
  _init_repo "$repo" "git@ghe.acme.com:acme/widget.git" "ghe.acme.com"
  _init_repo "$repo_pub" "git@ghe.acme.com:acme/widget.git"
  local old new
  old="$(_key_for_repo "$repo_pub")"
  new="$(_key_for_repo "$repo")"
  _seed_target "${old}-main" "$repo" "$old"
  _seed_target "${new}-main" "$repo" "$new"
  printf '{"decided":"already here"}\n' > "$STATE/pr/${new}-main/pr-comments/state.json"

  _run_migration

  [ -d "$STATE/pr/${old}-main" ]
  run cat "$STATE/pr/${new}-main/pr-comments/state.json"
  [[ "$output" == *"already here"* ]]
}

@test "a target another run holds is deferred, not skipped" {
  # DEFERRED rather than NOOP: the lock is released when that run ends, and a
  # recorded NOOP is never retried — so a machine that always has a run in
  # flight would retire the migration against targets it never touched.
  local repo="$TMPDIR/repo" repo_pub="$TMPDIR/pub"
  _init_repo "$repo" "git@ghe.acme.com:acme/widget.git" "ghe.acme.com"
  _init_repo "$repo_pub" "git@ghe.acme.com:acme/widget.git"
  local old; old="$(_key_for_repo "$repo_pub")"
  local dir; dir="$(_seed_target "${old}-main" "$repo" "$old")"

  # Hold the target's lock from another process for the migration's lifetime.
  python3 - "$REPO_ROOT" "$dir" <<'PY' &
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from core import run_lock
from pr import state as pr_state
with run_lock.acquire(Path(sys.argv[2]), "test:holder", pr_state.now_iso()):
    print("held", flush=True)
    time.sleep(10)
PY
  local holder=$!
  # Wait for the lock to actually be held rather than racing the subshell.
  local waited=0
  while [[ ! -f "$dir/run.lock" && "$waited" -lt 50 ]]; do sleep 0.1; waited=$((waited + 1)); done

  _run_migration
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  [ "$status" -eq 4 ]
  [ -d "$STATE/pr/${old}-main" ]
}

@test "a target whose worktree is gone is counted and named" {
  # The host lives only in the checkout's config, so a deleted worktree cannot
  # have its key recomputed. A stated limitation — say so rather than pass over
  # it silently.
  local repo="$TMPDIR/repo"
  _init_repo "$repo" "git@ghe.acme.com:acme/widget.git" "ghe.acme.com"
  local key; key="$(_key_for_repo "$repo")"
  _seed_target "${key}-main" "$TMPDIR/deleted-worktree" "$key"

  _run_migration

  [ "$status" -eq 3 ]
  [ -d "$STATE/pr/${key}-main" ]
}

@test "no targets root is a noop" {
  rm -rf "$STATE/pr"

  _run_migration

  [ "$status" -eq 3 ]
}
