#!/usr/bin/env bash
set -e
# Migration: carry review trails into the one trail root and drop the bats
# residue under logs/. Carries via _append_ledger (lib/migrations.sh), whose
# temp-file + mv swap means a failure partway through never leaves
# legacy.jsonl with a partial, unrepeatable append — a retry sees the
# original destination and source untouched.

migration_20260814_unify_trail_root() {
    # Defensive only: the framework always exports WORKBENCH_STATE_DIR, but
    # there is no `set -u` here, and an empty value would collapse trail_root
    # to "/trail", the review glob to "/reviews/*/trail.jsonl", and the rmdir
    # below to "/logs" on a script that deletes files.
    [[ -n "$WORKBENCH_STATE_DIR" ]] || return 0

    local trail_root="$WORKBENCH_STATE_DIR/trail"
    local legacy="$trail_root/legacy.jsonl"
    local carried=0 dropped=0

    # _append_ledger reads the destination with `cat "$dst" > "$tmp"`, which
    # fails if $legacy does not exist yet — create it once, up front.
    mkdir -p "$trail_root"
    touch "$legacy"

    # Review trails are real per-PR history: {repo, pr, branch} context, which
    # is exactly what `otto-log query --pr N` serves. One file rather than
    # monthly buckets — otto-log sorts every record by ts after loading, so a
    # single file reassembles into the same timeline.
    #
    # A source that fails to merge (e.g. unreadable) is warned about by
    # _append_ledger and left in place for the next sync to retry; it must
    # not take the rest of the carry down with it.
    local src
    for src in "$WORKBENCH_STATE_DIR"/reviews/*/trail.jsonl; do
        [[ -f "$src" ]] || continue
        if _append_ledger "$src" "$legacy"; then
            carried=$(( carried + 1 ))
        fi
    done

    # Measured against the real state root: 104,309 records across four tool
    # dirs — promote-scan (60,325 records, 60,320 test-tainted, 17M),
    # retro-scan (7,441 / 5,100 tainted, 2.4M), dream-scan (36,536 / 0
    # tainted, 7.7M), ci-check (7 / 0 tainted, 4K). Only promote-scan is
    # mostly the bats residue #682 fixed; dream-scan's 36,536 are genuine.
    # The drop is still right on value, not taint: dream-scan is 9,134
    # identical hook-run heartbeats ("found 0 memory files across 0 topics",
    # "found 1 signals", "report generated, 104 chars") with no repo, PR, or
    # decision content. Folding that in would tax every otto-log invocation
    # forever — legacy.jsonl carries no month in its name, so `--since` can
    # never skip it the way it skips a monthly file.
    local tool_dir
    for src in "$WORKBENCH_STATE_DIR"/logs/*/trail.jsonl; do
        [[ -f "$src" ]] || continue
        tool_dir="${src%/*}"
        rm -f "$src"
        rmdir "$tool_dir" 2>/dev/null || true
        dropped=$(( dropped + 1 ))
    done
    rmdir "$WORKBENCH_STATE_DIR/logs" 2>/dev/null || true

    if (( carried == 0 && dropped == 0 )); then
        success "No trails to carry into $trail_root"
        return 0
    fi
    if (( carried > 0 )); then
        success "Carried $carried review trail(s) into $legacy; dropped $dropped log trail(s)"
    else
        success "Dropped $dropped log trail(s)"
    fi
    # Per-worktree trails under <git-dir>/workbench/trail.jsonl are not carried:
    # finding them means enumerating every repo on the machine, and they die
    # with their worktree either way.
    warn "Per-worktree trails were not carried — they end with their worktree"
}

migration_20260814_unify_trail_root
