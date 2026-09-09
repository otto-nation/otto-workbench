#!/usr/bin/env bash
# Migration: rename the top-level issue_tracker key to issues in config.yml.
#
# The section held provider, team and jira_url, and `issue_tracker` described
# all three. It stopped describing the section when `labels` arrived: what a
# repo labels the issues its automation files is not a fact about which tracker
# is in use. WorkbenchConfig now spells the section `issues`.
#
# serde drops a key the surface does not have, so an unmigrated `issue_tracker`
# would go inert with nothing said at either end: the loader discards it and the
# reader waiting on it falls through to the built-in default. A machine that had
# named its tracker would be asked again where it files issues, which is the
# silence this rewrites the file to avoid.
#
# Only the machine-wide config.yml and the container file beside a bare repo's
# worktrees are rewritten — see the companion 20260909-rename-issue-tracker-
# container.sh for the second. A repo's own .workbench.yml is tracked in that
# repo and belongs to whoever committed it; rewriting one would put an
# uncommitted diff in a checkout during an unrelated sync. Those are reported
# instead, by 20260909-warn-issue-tracker-project.sh.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# writes a pre-split machine's copy — legacy shape and all — into the config
# root, and that can happen after this has already been recorded.

migration_20260909_rename_issue_tracker_key() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local legacy
  legacy="$(yq '.issue_tracker // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  # NOOP rather than deferred: the file is here and holds no legacy key, and
  # nothing writes .issue_tracker any more.
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

  info "Renaming issue_tracker to issues in config.yml"

  # A value already at the destination wins. It was written against the current
  # schema, so it is the answer in use, while the legacy copy is what the old
  # name left behind. The legacy key goes either way — it is dead once the new
  # one has a value, whichever write put it there.
  local existing
  existing="$(yq '.issues // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i '.issues = .issue_tracker' "$WORKBENCH_CONFIG_FILE" || return 1
  fi
  yq -i 'del(.issue_tracker)' "$WORKBENCH_CONFIG_FILE" || return 1

  success "config.yml now holds issues"
}
