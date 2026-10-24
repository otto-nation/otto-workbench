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

# _models_inherited_provider WIDER TIER — the provider WIDER, the config a wider
# scope holds, ran TIER's phases on: its agent.provider while it still has one,
# else the provider its models.TIER now names — where the machine-wide half of
# this migration moved that agent.provider. Empty when it names neither. The
# provider part is read as `agent.models.parse_ref` reads it: the text before
# the first slash, and only when it is shaped like a provider id.
_models_inherited_provider() {
  local wider="$1" tier="$2" provider value head
  [[ -n "$wider" && -f "$wider" ]] || return 0
  provider="$(yq '.agent.provider // ""' "$wider")" || return 1
  if [[ -n "$provider" ]]; then
    printf '%s' "$provider"
    return 0
  fi
  value="$(T="$tier" yq '.models[strenv(T)] // ""' "$wider")" || return 1
  [[ "$value" == */* ]] || return 0
  head="${value%%/*}"
  [[ "$head" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 0
  printf '%s' "$head"
}

# models_from_agent_model FILE [WIDER] — rewrite FILE as the header describes.
# WIDER is the config file a wider scope holds; when FILE names a model and no
# provider, each tier takes the provider WIDER ran it on, because the merged
# config ran FILE's model there. Returns 0 when it changed FILE and
# MIGRATION_NOOP when FILE names neither key. Shared by the container
# migration, which sources this file.
models_from_agent_model() {
  local file="$1" wider="${2:-}" model provider tier tier_provider ref
  model="$(yq '.agent.model // ""' "$file")" || return 1
  provider="$(yq '.agent.provider // ""' "$file")" || return 1
  [[ -n "$model" || -n "$provider" ]] || return "$MIGRATION_NOOP"

  if [[ -z "$model" ]]; then
    warn "$file: agent.provider '$provider' has no agent.model to carry it — set models.<tier> to $provider/<model>"
  fi
  for tier in deep standard light; do
    [[ -n "$model" ]] || break
    tier_provider="$provider"
    if [[ -z "$tier_provider" ]]; then
      tier_provider="$(_models_inherited_provider "$wider" "$tier")" || return 1
    fi
    ref="$model"
    [[ -z "$tier_provider" ]] || ref="$tier_provider/$model"
    T="$tier" R="$ref" yq -i '.models[strenv(T)] = (.models[strenv(T)] // strenv(R))' "$file" || return 1
  done
  # A reference reads the part before its first slash as the provider, so a
  # bare id that contains one would now name a different provider.
  if [[ -n "$model" && -z "$provider" && "$model" == */* ]]; then
    warn "$file: agent.model '$model' contains a slash, which models.* may read as provider/model — prefix the provider it runs on if '${model%%/*}' is not one"
  fi
  yq -i 'del(.agent.model) | del(.agent.provider)' "$file" || return 1
}

migration_20261024_models_from_agent_model() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"
  models_from_agent_model "$WORKBENCH_CONFIG_FILE" || return
  success "config.yml now names each tier's model (models.deep, models.standard, models.light)"
}
