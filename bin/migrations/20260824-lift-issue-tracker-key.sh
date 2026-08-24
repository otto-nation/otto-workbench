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
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# writes a pre-split machine's copy — legacy shape and all — into the config
# root, and that can happen after this has already been recorded. The version
# dated 20260819 asserted the re-seeded file would get another pass without
# asking for one; this marker is the request.
#
# Deferred rather than recorded while config.yml is absent. Adoption is not the
# only way the file arrives late: wb_config_ensure_file seeds it, /reuse writes
# to it, and a session or a hand edit can create it at any point. The 20260819
# version returned 0 on a machine that had no config.yml at all, was recorded
# for good, and had nothing left to lift when a session wrote the legacy shape
# into a new config.yml half an hour later — five days of sessions then read
# "Issue tracker: not configured" from the key this exists to move.
#
# Dated after that version so _prune_stale_migration_state drops the state entry
# it left behind and this one runs — see docs/execution-flow.md § Migrations.

migration_20260824_lift_issue_tracker_key() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local legacy
  legacy="$(yq '.review.issue_tracker // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  # NOOP rather than deferred: the file is here and holds no legacy key, and
  # nothing writes .review.issue_tracker any more — the fold that used to now
  # writes the top-level path. A copy of the old shape can still arrive by
  # adoption, which the marker above covers by forgetting this entry outright.
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

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
