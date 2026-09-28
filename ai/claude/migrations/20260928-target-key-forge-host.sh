#!/usr/bin/env bash
set -e
# Migration: rekey run targets whose key changed when the forge host and the
# unrewritten origin URL entered it.
#
# `<state_dir>/pr/<repo-key>-<branch-slug>/` holds state.json, run.lock and the
# pr-comments thread ledger. The ledger carries `classification`, `summary` and
# `decided_at` — triage decisions made locally that no API call reproduces — so
# a target left at a key nothing reads is not a cache miss, it is lost work that
# re-triages every open thread. `pr gc` only sees directories that still carry a
# state.json, so an unmigrated rename orphans state silently rather than loudly.
#
# Two causes, both from the same change:
#
#   1. A repo that declares `github.host` in .workbench.yml now hashes that
#      instance into its key, so two same-pathed repos on two GitHub instances
#      stop sharing one lock and one ledger.
#   2. The origin URL is now read with `config --get remote.origin.url` rather
#      than `remote get-url`, which applied `url.*.insteadOf` rewriting — so a
#      machine with a rewrite rule was keying on the rewritten spelling.
#
# Neither fires by default: an undeclared repo with no rewrite rule keys exactly
# as before, which is every target on a machine that has not opted in. The
# common outcome here is NOOP.
#
# Not a lazy rename at resolve time. `resolve_at` runs before the lock is taken
# (ai/bin/pr), `pr status` never locks at all, and flock follows the inode
# rather than the path — a directory renamed out from under a live run carries
# its lock with it while the run recreates the old path and takes an
# uncontended lock on a new inode, leaving two state.json files and two locks
# for one logical target.
#
# The key is derived by `pr.target` and the lock taken by `core.run_lock`, both
# called from Python. A second implementation of either in bash would move a
# target somewhere the tool does not look, which reads exactly like the state
# having been lost.

migration_20260928_target_key_forge_host() {
  local targets_root
  targets_root=$(_target_key_state_root)

  # NOOP, not DEFERRED: a machine with no targets root has never run `pr`, and
  # the root is created by a run rather than by a later component step — so
  # there is nothing here that a subsequent sync would find.
  [[ -d "$targets_root" ]] || return "$MIGRATION_NOOP"

  local report status=0
  report=$(_target_key_rekey "$targets_root") || status=$?

  # Busy is the one outcome that resolves later: another process holds the
  # target's lock right now, and the next sync takes it. Deferring rather than
  # recording a NOOP is what keeps a machine that always has a run in flight
  # from retiring the migration against targets it never touched.
  if _migration_lock_busy "$report"; then
    return "$MIGRATION_DEFERRED"
  fi

  [[ "$status" -eq 0 ]] || {
    warn "Could not rekey run targets: $report"
    return 1
  }

  local moved
  moved=$(_target_key_count "$report" moved)
  [[ "$moved" -gt 0 ]] || return "$MIGRATION_NOOP"

  success "Rekeyed $moved run target(s) for their forge host"
  printf '%s\n' "$report" | _target_key_lines
}

# _migration_lock_busy REPORT — true when the rekey stopped on a held lock.
#
# Named, and matched on a token the Python side emits rather than on free text,
# because bin/local/validate-migrations only lets MIGRATION_DEFERRED be returned
# from a guard it recognises — and this helper is one of the five it knows. That
# is deliberate: the set of conditions a migration may defer on stays enumerable
# in the validator instead of being any predicate a migration cares to write.
# The token is spelled here rather than in a file-scope readonly: the framework
# sources this file to find the function, so anything at file scope runs on
# every sync whether or not the migration does.
_migration_lock_busy() {
  [[ "$1" == *"LOCK_BUSY"* ]]
}

# _target_key_state_root — the directory holding per-target state.
#
# Asked of pr.target rather than spelled here: the state root is relocatable and
# `targets_root()` is what every reader resolves it through.
_target_key_state_root() {
  python3 - "$WORKBENCH_DIR" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target

print(pr_target.targets_root())
PY
}

# _target_key_rekey ROOT — move every target whose key changed, printing a report.
#
# One Python call rather than a loop in bash: the comparison needs the key rule,
# the rename needs the lock, and both live on the Python side. Prints one line
# per target it acted on or refused, plus a trailing tally.
_target_key_rekey() {
  python3 - "$WORKBENCH_DIR" "$1" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))

from core import run_lock
from pr import state as pr_state
from pr import target as pr_target

root = Path(sys.argv[2])
moved = noop = skipped = unresolved = 0

for target in sorted(p for p in root.iterdir() if p.is_dir()):
    state_file = target / "state.json"
    if not state_file.is_file():
        # No state.json means no worktree_root, and the host lives only in that
        # checkout's config and remote. Counted and named rather than passed
        # over: the operator is the only one who can say where it belongs.
        unresolved += 1
        print(f"UNRESOLVED\t{target.name}\tno state.json")
        continue
    try:
        identity = json.loads(state_file.read_text()).get("identity", {})
    except (OSError, ValueError) as exc:
        unresolved += 1
        print(f"UNRESOLVED\t{target.name}\tunreadable state.json: {exc}")
        continue

    worktree = identity.get("worktree_root", "")
    if not worktree or not Path(worktree).is_dir():
        # A deleted worktree cannot have its key recomputed, because the
        # declaration and the remote both lived inside it. Stated limitation,
        # not deferred work — nothing later recovers it.
        unresolved += 1
        print(f"UNRESOLVED\t{target.name}\tworktree gone: {worktree or '(none)'}")
        continue

    resolved = pr_target.repo_identity_from_origin(worktree)
    if not resolved:
        unresolved += 1
        print(f"UNRESOLVED\t{target.name}\tno origin in {worktree}")
        continue

    # The branch half of the directory name is whatever follows the old key, so
    # it is carried across rather than recomputed: recomputing it would fold a
    # second rule into a migration that exists for one.
    old_key = identity.get("repo_key", "")
    suffix = target.name[len(old_key):] if old_key and target.name.startswith(old_key) else ""
    if not suffix:
        branch = identity.get("branch", "")
        suffix = f"-{pr_target.slug(branch)}" if branch else ""
    want = f"{resolved.key}{suffix}"

    if want == target.name:
        noop += 1
        continue

    destination = root / want
    if destination.exists():
        # Two ledgers for one target cannot be merged — each is a record of what
        # was decided, and picking one silently discards the other's decisions.
        skipped += 1
        print(f"SKIPPED\t{target.name}\tdestination exists: {want}")
        continue

    try:
        # The OLD directory's lock, held across the rename: a run that already
        # holds it is mid-flight against this target, and flock follows the
        # inode, so renaming under it would hand the live run a directory that
        # is no longer the one anyone else resolves to.
        with run_lock.acquire(target, "migration:target-key", pr_state.now_iso()):
            target.rename(destination)
    except run_lock.LockBusy:
        print(f"LOCK_BUSY\t{target.name}\tanother run holds this target")
        break
    except OSError as exc:
        print(f"FAILED\t{target.name}\t{exc}")
        sys.exit(1)

    moved += 1
    print(f"MOVED\t{target.name}\t{want}")

print(f"TALLY\tmoved={moved}\tnoop={noop}\tskipped={skipped}\tunresolved={unresolved}")
PY
}

# _target_key_count REPORT FIELD — read one number out of the trailing tally.
_target_key_count() {
  printf '%s\n' "$1" | awk -F'\t' -v want="$2" '
    $1 == "TALLY" {
      for (i = 2; i <= NF; i++) {
        split($i, kv, "=")
        if (kv[1] == want) { print kv[2]; exit }
      }
    }
  '
}

# _target_key_lines — the per-target detail, indented under the success line.
#
# Only the outcomes an operator can act on: a skipped destination needs a human
# to reconcile two ledgers, and an unresolved target is one whose forge nothing
# can now recover. A moved target needs no line of its own — the tally counted it.
_target_key_lines() {
  awk -F'\t' '
    $1 == "SKIPPED"    { printf "  skipped %s — %s\n", $2, $3 }
    $1 == "UNRESOLVED" { unresolved++ }
    END { if (unresolved) printf "  %d target(s) could not be keyed — their worktrees are gone\n", unresolved }
  '
}
