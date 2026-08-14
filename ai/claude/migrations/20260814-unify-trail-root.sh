#!/usr/bin/env bash
set -e
# Migration: carry review trails into the one trail root and drop the bats
# residue under logs/. Idempotent — each source is appended and then removed,
# so an interrupted run resumes without duplicating what it already carried.

migration_20260814_unify_trail_root() {
    local trail_root="$WORKBENCH_STATE_DIR/trail"
    local legacy="$trail_root/legacy.jsonl"
    local carried=0 dropped=0

    # Append one trail to legacy.jsonl and remove it. A source that does not end
    # in a newline would fuse its last record with the first one after it, and
    # the reader silently drops lines it cannot parse.
    _carry_trail() {
        local src="$1"
        cat "$src" >> "$legacy"
        if [[ -n "$(tail -c 1 "$legacy")" ]]; then
            printf '\n' >> "$legacy"
        fi
        rm -f "$src"
        carried=$(( carried + 1 ))
    }

    # Review trails are real per-PR history: {repo, pr, branch} context, which
    # is exactly what `otto-log query --pr N` serves. One file rather than
    # monthly buckets — otto-log sorts every record by ts after loading, so a
    # single file reassembles into the same timeline.
    local src
    for src in "$WORKBENCH_STATE_DIR"/reviews/*/trail.jsonl; do
        [[ -f "$src" ]] || continue
        mkdir -p "$trail_root"
        touch "$legacy"
        _carry_trail "$src"
    done

    # Log trails are ~29 MB of bats residue from a bug fixed in #682, holding
    # about five genuine records. Folding them in would put that noise into the
    # one file every otto-log invocation reads. Deleting is not data loss; it is
    # declining to migrate a leak.
    local tool_dir
    for src in "$WORKBENCH_STATE_DIR"/logs/*/trail.jsonl; do
        [[ -f "$src" ]] || continue
        tool_dir="$(dirname "$src")"
        rm -f "$src"
        rmdir "$tool_dir" 2>/dev/null || true
        dropped=$(( dropped + 1 ))
    done
    rmdir "$WORKBENCH_STATE_DIR/logs" 2>/dev/null || true

    if (( carried == 0 && dropped == 0 )); then
        success "No trails to carry into $trail_root"
        return 0
    fi
    success "Carried $carried review trail(s) into $legacy; dropped $dropped log trail(s)"
    # Per-worktree trails under <git-dir>/workbench/trail.jsonl are not carried:
    # finding them means enumerating every repo on the machine, and they die
    # with their worktree either way.
    warn "Per-worktree trails were not carried — they end with their worktree"
}

migration_20260814_unify_trail_root
