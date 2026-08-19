#!/usr/bin/env bash
# Migration: lift review.issue_tracker to the top level of config.yml.
#
# 20260814-unify-workbench-config originally folded .claude/review.yml into
# .review.issue_tracker, inheriting the namespace of the file it drained. Where
# a repo files issues is a fact about the repo rather than a review setting —
# the SessionStart context line and every rule in issue-tracker.md read it, and
# only two of its callers are reviews — so WorkbenchConfig now holds it at the
# top level. That fold writes the new path, which leaves the machines that ran
# the old one holding a key nothing reads; this moves theirs across.
#
# Not adoption-sensitive: this rewrites keys inside config.yml, and config.yml
# is a _LEGACY_CONFIG_ENTRIES name that adoption re-seeds whole. A re-seeded
# file arrives with whatever shape it already had, and another pass lifts it.

migration_20260819_lift_issue_tracker_key() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return 0

  local legacy
  legacy="$(yq '.review.issue_tracker // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  [[ -n "$legacy" ]] || return 0

  info "Lifting review.issue_tracker to the top level of config.yml"

  # A value already at the destination wins. It was written against the current
  # schema, so it is the answer in use, while the legacy copy is what the old
  # fold left behind. The legacy key goes either way — it is dead once the
  # top-level one has a value, whichever write put it there.
  local existing
  existing="$(yq '.issue_tracker // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i '.issue_tracker = .review.issue_tracker' "$WORKBENCH_CONFIG_FILE" || return 1
  fi

  # The emptied review section goes too: the old fold created it on machines
  # that set nothing else under review, and a bare `review: {}` left behind
  # reads as a setting someone chose. A review holding anything else is kept.
  yq -i 'del(.review.issue_tracker)' "$WORKBENCH_CONFIG_FILE" || return 1
  yq -i 'del(.review | select(length == 0))' "$WORKBENCH_CONFIG_FILE" || return 1

  success "config.yml now holds issue_tracker at the top level"
}
