#!/usr/bin/env bash
set -e
# project-scoped: renames a file inside each repo, so being done is a fact about
# a repo and not about the machine.
# Migration: rename .claude/context.md to .claude/architecture.md in every repo
# the machine knows about. A repo already renamed, or one that never had the
# file, answers MIGRATION_NOOP rather than 0 — every worktree registers itself
# the first time anything runs in it, and a branch cut from a main that already
# holds architecture.md is a visit that can never rename anything. Reported as
# success, that is a "Migration applied" line on every sync for as long as the
# machine keeps making worktrees, each naming a different count and none of
# them meaning work was done.
#
# Dated later than the rename it performs, on purpose. The first version searched
# for projects itself, found none on a machine whose repos it did not guess at,
# and recorded itself applied all the same. _prune_stale_migration_state drops the
# state entry for a filename that no longer exists, so re-dating the file is what
# gave the corrected version a run on the machines the original passed over.
#
# The loop over the registry it used to run is the framework's now: the marker
# above buys one state entry per repo, which is what lets a repo that registers
# after this file first ran still receive it.

migration_20260819_context_to_architecture() {
  local project_dir="$1"
  local old="$project_dir/.claude/context.md"
  local new="$project_dir/.claude/architecture.md"

  if [[ ! -f "$old" || -f "$new" ]]; then
    return "$MIGRATION_NOOP"
  fi

  mv "$old" "$new"
  success "Renamed .claude/context.md → architecture.md in $project_dir"
}
