#!/usr/bin/env bash
# Migration: carry the model ids ~/.env.local exports into config.yml's models.*.
#
# Claude Code's and Pi's syncs take their models from models.* now, and nothing
# reads the AI_*_MODEL exports (or the ANTHROPIC_* names those were renamed
# from) any more. Each value moves to the key LEGACY_MODEL_ENV_VARS pairs it
# with — AI_MODEL to models.session, AI_OPUS_MODEL to models.deep,
# AI_SONNET_MODEL to models.standard, AI_HAIKU_MODEL to models.light:
#
# 1. The key unset → it takes the value, and the export is deleted.
# 2. The key already names that model → the export is deleted. Compared by
#    id: the provider and level a tier carries were never in the export, so
#    `google-vertex-claude/claude-opus-5-5:high` names `claude-opus-5-5`.
# 3. The key holds another value → both are left as they are, and every sync
#    names the export (ai_warn_model_exports) until the operator picks one.
#
# Sorted ahead of 20261024-models-from-agent-model, which fills only the tiers
# this leaves unset: a machine that set a tier here keeps that tier's model —
# the Claude alias it ran on, and usually the cheaper one for light phases —
# rather than every tier collapsing onto agent.model. A machine that already
# ran that migration has every tier set, and case 3 reports what differs.
#
# A value takes agent.provider when the config still has one, the provider the
# workbench's phases ran on, as that migration does; the Claude view drops a
# provider the Claude CLI serves, so its aliases read the same bare id. With no
# agent.provider the value is carried bare, as both harnesses took it.
#
# Only active `export` lines are read and deleted, the lines read_env_local_var
# reads — a commented one was never in effect.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# can write a pre-migration copy — no models.* — back into the config root
# after this has run, and that copy needs the same carry-over.

# _models_ref_id REF — the model id in REF, read as `agent.models.parse_ref`
# reads it: the provider is the text before the first slash only when shaped
# like a provider id, and a trailing `:level` only when it is a Thinking value.
_models_ref_id() {
  local ref="$1" head="${1%%/*}"
  if [[ "$ref" == */* && "$head" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    ref="${ref#*/}"
  fi
  if [[ "$ref" == *:* ]]; then
    case "${ref##*:}" in
      off|minimal|low|medium|high|xhigh|max) ref="${ref%:*}" ;;
    esac
  fi
  printf '%s' "$ref"
}

migration_20261023_models_from_env_local() {
  [[ -f "$ENV_LOCAL_FILE" ]] || return "$MIGRATION_DEFERRED"

  local pair var key value current provider ref changed=""
  for pair in $LEGACY_MODEL_ENV_VARS; do
    var="${pair%%:*}"
    key="${pair#*:}"
    value="$(read_env_local_var "$var")"
    [[ -n "$value" ]] || continue

    wb_config_ensure_file || return 1
    current="$(K="$key" yq '.models[strenv(K)] // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
    if [[ -z "$current" ]]; then
      provider="$(yq '.agent.provider // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
      ref="$value"
      [[ -z "$provider" ]] || ref="$provider/$value"
      K="$key" V="$ref" yq -i '.models[strenv(K)] = strenv(V)' "$WORKBENCH_CONFIG_FILE" || return 1
      current="$ref"
    fi
    [[ "$(_models_ref_id "$current")" == "$value" ]] || continue

    sed_i "/^export ${var}=/d" "$ENV_LOCAL_FILE"
    changed=1
  done

  [[ -n "$changed" ]] || return "$MIGRATION_NOOP"
  success "config.yml now names the models ~/.env.local exported (models.*)"
}
