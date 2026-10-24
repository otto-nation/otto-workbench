#!/usr/bin/env bash
# Migration: carry agent.model and agent.provider into the models tiers.
#
# A phase now reads its model from models.<tier>, for the tier its spec names,
# and agent.model and agent.provider are gone. Every phase ran agent.model on
# agent.provider, so every tier takes that model: no phase changes model on
# upgrade, and moving the light phases to a cheaper one is then one key.
#
# 1. agent.model set → each unset models.<tier> = [agent.provider/]agent.model.
#    A tier already set keeps its value: it was written against the new schema.
# 2. agent.provider set with no agent.model → warned: Pi refused that pairing
#    already, and with no model there is no reference to carry the provider on.
# 3. Both keys removed, so `config status` stops reporting them as strays.
#
# Only the machine-wide config.yml is rewritten here. The container file is
# 20261024-models-from-agent-model-container.sh; a repo's own .workbench.yml is
# reported, not rewritten, by 20261024-warn-agent-model-project.sh.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# can write a pre-migration copy — still carrying agent.model — back into the
# config root after this has run, and that copy needs the same carry-over.

# models_from_agent_model FILE [INHERITED_PROVIDER] — rewrite FILE as the header
# describes. INHERITED_PROVIDER is the agent.provider a wider scope set, joined
# onto FILE's model when FILE names none of its own: the merged config ran the
# model on it. Returns 0 when it changed FILE and MIGRATION_NOOP when FILE names
# neither key. Shared by the container migration, which sources this file.
models_from_agent_model() {
  local file="$1" inherited="${2:-}" model provider ref
  model="$(yq '.agent.model // ""' "$file")" || return 1
  provider="$(yq '.agent.provider // ""' "$file")" || return 1
  [[ -n "$model" || -n "$provider" ]] || return "$MIGRATION_NOOP"
  [[ -n "$provider" ]] || provider="$inherited"

  if [[ -n "$model" ]]; then
    ref="$model"
    [[ -z "$provider" ]] || ref="$provider/$model"
    # A reference reads the part before its first slash as the provider, so a
    # bare id that contains one would now name a different provider.
    if [[ -z "$provider" && "$model" == */* ]]; then
      warn "$file: agent.model '$model' contains a slash, which models.* reads as provider/model — prefix the provider it runs on if '${model%%/*}' is not one"
    fi
    R="$ref" yq -i '
      .models.deep = (.models.deep // strenv(R)) |
      .models.standard = (.models.standard // strenv(R)) |
      .models.light = (.models.light // strenv(R))
    ' "$file" || return 1
  else
    warn "$file: agent.provider '$provider' has no agent.model to carry it — set models.<tier> to $provider/<model>"
  fi
  yq -i 'del(.agent.model) | del(.agent.provider)' "$file" || return 1
}

migration_20261024_models_from_agent_model() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"
  models_from_agent_model "$WORKBENCH_CONFIG_FILE" || return
  success "config.yml now names each tier's model (models.deep, models.standard, models.light)"
}
