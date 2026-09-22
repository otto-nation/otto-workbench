#!/usr/bin/env bash
# Migration: rename issues.jira_url to issues.base_url in config.yml.
#
# The key named one vendor for a value that is not about that vendor: it held
# the host a tracker instance lives at, which GitHub Enterprise needs on the
# same terms Jira does. The path beneath that host — Jira's /browse/ID — is
# fixed per provider and now lives beside each provider's lookup in
# review/issue.py, so the config key is the host alone and is spelled for what
# it is. IssuesConfig now calls it base_url.
#
# The value is carried across untouched: both names held the instance host, so
# a machine that had named its Jira tenant keeps the same links afterwards.
#
# serde drops a key the surface does not have, so an unmigrated jira_url would
# go inert with nothing said at either end — the loader discards it, and a
# review of a Jira-tracked repo silently stops linking the issue it names. A
# missing link is not an error anywhere downstream, which is why the rewrite
# happens here rather than being left to be noticed.
#
# Only the machine-wide config.yml and the container file beside a bare repo's
# worktrees are rewritten — see the companion 20260922-rename-issues-jira-url-
# container.sh for the second. A repo's own .workbench.yml is tracked in that
# repo and belongs to whoever committed it; rewriting one would put an
# uncommitted diff in a checkout during an unrelated sync. Those are reported
# instead, by 20260922-warn-issues-jira-url-project.sh.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# writes a pre-split machine's copy — legacy shape and all — into the config
# root, and that can happen after this has already been recorded.

migration_20260922_rename_issues_jira_url() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local legacy
  legacy="$(yq '.issues.jira_url // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  # NOOP rather than deferred: the file is here and holds no legacy key, and
  # nothing writes .issues.jira_url any more.
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

  info "Renaming issues.jira_url to issues.base_url in config.yml"

  # A value already at the destination wins. It was written against the current
  # schema, so it is the answer in use, while the legacy copy is what the old
  # name left behind. The legacy key goes either way — it is dead once the new
  # one has a value, whichever write put it there.
  local existing
  existing="$(yq '.issues.base_url // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i '.issues.base_url = .issues.jira_url' "$WORKBENCH_CONFIG_FILE" || return 1
  fi
  yq -i 'del(.issues.jira_url)' "$WORKBENCH_CONFIG_FILE" || return 1

  success "config.yml now holds issues.base_url"
}
