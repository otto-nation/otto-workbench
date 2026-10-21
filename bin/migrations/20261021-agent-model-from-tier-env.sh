#!/usr/bin/env bash
# Migration: carry the tier model the workbench used to dispatch into agent.model.
#
# Every phase defaulted to the tier word `sonnet`, which the workbench rewrote
# through AI_SONNET_MODEL (or the pre-rename ANTHROPIC_DEFAULT_SONNET_MODEL)
# from ~/.env.local. That rewriting is gone: a phase now names no model unless
# the configuration does. Without this, a machine that only ever set the tier
# variable would silently switch to the backend's own default model (often
# Opus), and a Pi machine with agent.provider set would stop at preflight.
#
# 1. agent.model empty and a sonnet tier value exported → agent.model = that
#    value: the model every phase actually ran.
# 2. agent.model empty, no tier value, Claude backend → agent.model = sonnet,
#    the claude CLI's own alias and what every phase ran there.
# 3. Pi backend with neither → left alone, with a warning naming agent.model.
# 4. Pi backend: agent.model and agent.phases.*.model holding a tier word are
#    replaced by that tier's exported value when there is one (a literal tier
#    word is not an id any Pi catalogue lists); otherwise left, with a warning.
#    The claude CLI resolves tier words itself, so they stay on that backend.
#
# Only the machine-wide config.yml is rewritten: a repo's .workbench.yml
# belongs to that repo, and preflight names a leftover tier word there.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# can write a pre-migration copy — no agent.model — back into the config root
# after this has run, and that copy needs the same carry-over.

# _agent_model_tier_value TIER — the exported value for TIER (SONNET, OPUS,
# HAIKU) in ~/.env.local: AI_<TIER>_MODEL, else ANTHROPIC_DEFAULT_<TIER>_MODEL.
# Commented lines are ignored; surrounding quotes are stripped.
_agent_model_tier_value() {
  local tier="$1" key line value
  [[ -f "$ENV_LOCAL_FILE" ]] || return 0
  for key in "AI_${tier}_MODEL" "ANTHROPIC_DEFAULT_${tier}_MODEL"; do
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_LOCAL_FILE" | tail -1 || true)"
    [[ -n "$line" ]] || continue
    value="${line#*"${key}"=}"
    if [[ "$value" =~ ^\"([^\"]*)\" ]]; then value="${BASH_REMATCH[1]}"
    elif [[ "$value" =~ ^\'([^\']*)\' ]]; then value="${BASH_REMATCH[1]}"
    else value="${value%%[[:space:]]#*}"; value="${value%"${value##*[![:space:]]}"}"; fi
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done
}

# _agent_model_backend — pi or claude, decided as selected_backend_or_claude()
# does: AI_BACKEND, then agent.backend, then claude.
_agent_model_backend() {
  local backend="${AI_BACKEND:-}"
  if [[ -z "$backend" ]]; then
    backend="$(yq '.agent.backend // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  fi
  [[ "$backend" == "pi" ]] && printf 'pi' || printf 'claude'
}

# _agent_model_replace_tier_word PATH CHANGED_VAR — rewrite the model at yq PATH
# when it is a bare tier word with an exported value, setting CHANGED_VAR to 1.
# A nameref rather than stdout, so the warning below is printed, not captured.
_agent_model_replace_tier_word() {
  local path="$1" current value
  local -n __changed="$2"
  current="$(yq "${path} // \"\"" "$WORKBENCH_CONFIG_FILE")" || return 1
  case "$current" in
    sonnet|opus|haiku) ;;
    *) return 0 ;;
  esac
  value="$(_agent_model_tier_value "$(printf '%s' "$current" | tr '[:lower:]' '[:upper:]')")"
  if [[ -z "$value" ]]; then
    warn "${path#.} is the tier word '$current', which no longer resolves; set it to a model id"
    return 0
  fi
  V="$value" yq -i "${path} = strenv(V)" "$WORKBENCH_CONFIG_FILE" || return 1
  __changed=1
}

migration_20261021_agent_model_from_tier_env() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local changed="" model tier_value backend phase
  model="$(yq '.agent.model // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  backend="$(_agent_model_backend)" || return 1

  if [[ -z "$model" ]]; then
    tier_value="$(_agent_model_tier_value SONNET)"
    if [[ -n "$tier_value" ]]; then
      V="$tier_value" yq -i '.agent.model = strenv(V)' "$WORKBENCH_CONFIG_FILE" || return 1
      changed=1
    elif [[ "$backend" == "claude" ]]; then
      yq -i '.agent.model = "sonnet"' "$WORKBENCH_CONFIG_FILE" || return 1
      changed=1
    else
      warn "agent.model is unset: Pi will use its own default model, and refuses a provider without one — set agent.model"
    fi
  elif [[ "$backend" == "pi" ]]; then
    _agent_model_replace_tier_word '.agent.model' changed || return 1
  fi

  if [[ "$backend" == "pi" ]]; then
    local phases
    phases="$(yq '.agent.phases // {} | keys | .[]' "$WORKBENCH_CONFIG_FILE")" || return 1
    for phase in $phases; do
      _agent_model_replace_tier_word ".agent.phases.${phase}.model" changed || return 1
    done
  fi

  [[ -n "$changed" ]] || return "$MIGRATION_NOOP"
  success "config.yml now names the model phases ran on (agent.model)"
}
