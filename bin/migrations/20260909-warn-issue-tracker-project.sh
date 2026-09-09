#!/usr/bin/env bash
# checkout-scoped: the file it reports on is the one at this work tree's own
# root, so having reported it is a fact about the checkout.
# Migration: report a repo .workbench.yml still holding the old issue_tracker
# key, rather than rewriting it.
#
# The two companion migrations move the machine-wide config.yml and the
# container file. This one deliberately changes nothing. A repo's .workbench.yml
# is tracked in that repo: rewriting it would put an uncommitted diff in a
# checkout during a sync the user ran for another reason, and the fix belongs in
# a commit its owner makes.
#
# Reporting is not nothing, though. serde drops a key the surface does not have,
# so the alternative to a warning is that the repo's tracker setting disappears
# with no error at either end — the loader discards it, and the next `pr
# comments` asks where the repo files issues as though it had never been told.
# The line below is the only place that becomes visible.
#
# Recorded once per checkout so the warning is not repeated on every sync
# forever. A repo that renames the key afterwards is already reading the new
# one; a repo that never does keeps the default, having been told once why.

migration_20260909_warn_issue_tracker_project() {
  local work_tree="$1" file legacy

  file="$work_tree/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: most repos have no .workbench.yml and never will,
  # and one written later is written by someone reading today's schema.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"

  legacy="$(yq '.issue_tracker // ""' "$file")" || return 1
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

  warn "$file still names issue_tracker, which nothing reads — rename it to issues"
  # Success: the report is the whole job, and it is done.
}
