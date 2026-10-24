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

migration_20261024_models_from_agent_model_container() {
  local project_dir="$1" container file
  if ! declare -F models_from_agent_model >/dev/null; then
    # shellcheck source=20261024-models-from-agent-model.sh
    . "$(dirname "${BASH_SOURCE[0]}")/20261024-models-from-agent-model.sh" || return 1
  fi
  container="$(git_bare_container "$project_dir")" || return "$MIGRATION_NOOP"
  file="$container/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: a container that has no file never grows one on
  # its own, and one written later is written against today's schema.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"
  # The machine-wide agent.provider, while it is still there to read: the
  # merged config ran this container's model on it. Once the machine-wide half
  # has run, that provider is gone, and its warning named it for the operator.
  local inherited=""
  if [[ -f "$WORKBENCH_CONFIG_FILE" ]]; then
    inherited="$(yq '.agent.provider // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  fi
  models_from_agent_model "$file" "$inherited" || return
  success "$file now names each tier's model (models.*)"
}
