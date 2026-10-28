#!/usr/bin/env bash
# repo-scoped: the container config belongs to the repo every worktree shares,
# so being done is a fact about that repo rather than about any one checkout.
# Migration: rename issues.jira_url to issues.base_url in the .workbench.yml
# beside a bare repo's worktrees.
#
# The companion 20260922-rename-issues-jira-url.sh has the reason for the rename
# and what an unmigrated key costs. This is the container half of it: the scope
# that holds a repo's answer without being inside any checkout of it, and
# therefore the one a rewrite cannot dirty.
#
# A repo's own .workbench.yml is deliberately left alone — it is tracked, and
# rewriting one would put an uncommitted diff in someone's checkout during an
# unrelated sync. 20260922-warn-issues-jira-url-project.sh reports those.
#
# Repo-scoped rather than checkout-scoped because one container serves every
# worktree of a repo: visiting each checkout would rewrite the same file N times
# and leave N state lines behind it. The framework hands over one of the repo's
# registered work trees, which is all this needs — it resolves the container
# from there.

migration_20260922_rename_issues_jira_url_container() {
  local project_dir="$1" container file legacy existing

  container="$(git_bare_container "$project_dir")" || return "$MIGRATION_NOOP"

  file="$container/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: a plain clone has no container at all, and a
  # container nobody has recorded an answer for never grows this file on its
  # own. Deferring would ask the same question of the same repo on every sync
  # for good.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"

  legacy="$(yq '.issues.jira_url // ""' "$file")" || return 1
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

  # A value already at the destination wins, for the reason the machine-wide
  # half gives: it was written against the current schema, while the legacy copy
  # is what the old name left behind.
  existing="$(yq '.issues.base_url // ""' "$file")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i '.issues.base_url = .issues.jira_url' "$file" || return 1
  fi
  yq -i 'del(.issues.jira_url)' "$file" || return 1

  success "Renamed issues.jira_url to issues.base_url in $file"
}
