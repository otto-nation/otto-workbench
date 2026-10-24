#!/usr/bin/env bash
# repo-scoped: the container config belongs to the repo every worktree shares,
# so being done is a fact about that repo rather than about any one checkout.
# Migration: carry agent.model and agent.provider into the models tiers in the
# .workbench.yml beside a bare repo's worktrees.
#
# The companion 20261024-models-from-agent-model.sh has the reason and the
# rewrite, which this sources rather than restating: an applied migration is
# skipped before it is loaded, so its function cannot be assumed present. This
# is the container half — the scope that holds a repo's answer without being
# inside any checkout of it, and so the one a rewrite cannot dirty.
#
# A repo's own .workbench.yml is left alone; it is tracked, and
# 20261024-warn-agent-model-project.sh reports it instead.

# _models_container DIR — the bare-repo container DIR sits in, or a non-zero
# status when DIR is not in that layout. resolve-worktree answers 0 or 1 for a
# container (with or without a worktree on its default branch) and 2 or 64 for
# anything else, which is never rewritten.
_models_container() {
  local dir="$1" common container rc=0
  git_env_clear
  common="$(git_shared_dir "$dir")" || return 1
  container="$(dirname "$common")"
  "$BIN_SRC_DIR/resolve-worktree" "$container" >/dev/null 2>&1 || rc=$?
  (( rc == 0 || rc == 1 )) || return 1
  printf '%s' "$container"
}

migration_20261024_models_from_agent_model_container() {
  local project_dir="$1" container file
  if ! declare -F models_from_agent_model >/dev/null; then
    # shellcheck source=20261024-models-from-agent-model.sh
    . "$(dirname "${BASH_SOURCE[0]}")/20261024-models-from-agent-model.sh" || return 1
  fi
  container="$(_models_container "$project_dir")" || return "$MIGRATION_NOOP"
  file="$container/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: a container that has no file never grows one on
  # its own, and one written later is written against today's schema.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"
  models_from_agent_model "$file" || return
  success "$file now names each tier's model (models.*)"
}
