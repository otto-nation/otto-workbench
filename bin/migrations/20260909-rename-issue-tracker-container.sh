#!/usr/bin/env bash
# repo-scoped: the container config belongs to the repo every worktree shares,
# so being done is a fact about that repo rather than about any one checkout.
# Migration: rename issue_tracker to issues in the .workbench.yml beside a bare
# repo's worktrees.
#
# The companion 20260909-rename-issue-tracker-key.sh has the reason for the
# rename and what an unmigrated key costs. This is the container half of it: the
# scope that holds a repo's answer without being inside any checkout of it, and
# therefore the one a rewrite cannot dirty.
#
# A repo's own .workbench.yml is deliberately left alone — it is tracked, and
# rewriting one would put an uncommitted diff in someone's checkout during an
# unrelated sync. 20260909-warn-issue-tracker-project.sh reports those instead.
#
# Repo-scoped rather than checkout-scoped because one container serves every
# worktree of a repo: visiting each checkout would rewrite the same file N times
# and leave N state lines behind it. The framework hands over one of the repo's
# registered work trees, which is all this needs — it resolves the container
# from there.

# _rename_issue_tracker_container DIR — the bare-repo container DIR sits in, or
# a non-zero status when DIR is not in that layout.
#
# The shared git dir comes from lib/git_layout.sh, which owns that lookup and
# clears the git environment inside its own subshell. The clear is repeated here
# for resolve-worktree below: it discovers from the directory it is handed, and a
# sync run from inside a git hook has GIT_DIR exported — which git reads ahead of
# any directory, so the answer would be the hook's repository. Called through a
# command substitution, which is what scopes the clear to the lookup.
_rename_issue_tracker_container() {
  local dir="$1" common container rc=0

  git_env_clear

  common="$(git_shared_dir "$dir")" || return 1
  container="$(dirname "$common")"

  # resolve-worktree owns "is this a bare-repo container" in bash. 0 is a
  # container with a worktree on its default branch and 1 is one whose default
  # branch has no worktree — both can hold a config file. 2 says it is an
  # ordinary repo or worktree, and 64 says the path is gone; mistaking either
  # for a container would rewrite a real checkout's tracked .workbench.yml,
  # which is the one file this migration exists to leave alone.
  "$BIN_SRC_DIR/resolve-worktree" "$container" >/dev/null 2>&1 || rc=$?
  (( rc == 0 || rc == 1 )) || return 1

  printf '%s' "$container"
}

migration_20260909_rename_issue_tracker_container() {
  local project_dir="$1" container file legacy existing

  container="$(_rename_issue_tracker_container "$project_dir")" \
    || return "$MIGRATION_NOOP"

  file="$container/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: a plain clone has no container at all, and a
  # container nobody has recorded an answer for never grows this file on its
  # own. Deferring would ask the same question of the same repo on every sync
  # for good.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"

  legacy="$(yq '.issue_tracker // ""' "$file")" || return 1
  [[ -n "$legacy" ]] || return "$MIGRATION_NOOP"

  # A value already at the destination wins, for the reason the machine-wide
  # half gives: it was written against the current schema, while the legacy copy
  # is what the old name left behind.
  existing="$(yq '.issues // ""' "$file")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i '.issues = .issue_tracker' "$file" || return 1
  fi
  yq -i 'del(.issue_tracker)' "$file" || return 1

  success "Renamed issue_tracker to issues in $file"
}
