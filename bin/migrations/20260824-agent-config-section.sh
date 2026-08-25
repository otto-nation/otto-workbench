#!/usr/bin/env bash
# Migration: move the agent-sizing keys out of review into a top-level agent section.
#
# model, thinking, provider and phases sized a review because reviews were the
# only thing the workbench ran an agent for. They are not review settings — a
# phase is any agent invocation the workbench sizes, and the comment, CI, describe
# and rebase passes each run one — so WorkbenchConfig now holds them under
# `agent`, leaving `review` with `effort` alone.
#
# serde drops a key the surface does not have, so an unmigrated `review.model`
# would go inert with nothing said at either end: the loader discards it and the
# reader waiting on it falls through to the built-in default. That silence is why
# this rewrites the file rather than leaving the old shape to be noticed.
#
# Only the machine-wide config.yml is rewritten. A repo's own .workbench.yml is
# tracked in that repo and belongs to whoever committed it; `otto-workbench
# config status` reports a key the surface does not have, which is how a project
# file holding the old shape surfaces.
#
# adoption-sensitive: config.yml is a _LEGACY_CONFIG_ENTRIES name, so adoption
# writes a pre-split machine's copy — legacy shape and all — into the config root,
# and that can happen after this has already been recorded.

# Carry one review.<key> across to agent.<key>.
#
# A value already at the destination wins: it was written against the current
# schema, so it is the answer in use, while the review copy is what the old shape
# left behind. The legacy key goes either way — it is dead once the destination
# has a value, whichever write put it there.
#
# KEY is a literal from the caller's own list, so interpolating it into the yq
# expression introduces nothing the migration did not already spell out.
_agent_config_section_move() {
  local key="$1" legacy existing

  legacy="$(yq ".review.$key // \"\"" "$WORKBENCH_CONFIG_FILE")" || return 1
  [[ -n "$legacy" ]] || return 0

  existing="$(yq ".agent.$key // \"\"" "$WORKBENCH_CONFIG_FILE")" || return 1
  if [[ -z "$existing" ]]; then
    yq -i ".agent.$key = .review.$key" "$WORKBENCH_CONFIG_FILE" || return 1
  fi

  yq -i "del(.review.$key)" "$WORKBENCH_CONFIG_FILE" || return 1
}

migration_20260824_agent_config_section() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local legacy
  legacy="$(yq '[.review.model, .review.thinking, .review.provider, .review.phases]
                | map(select(. != null)) | length' "$WORKBENCH_CONFIG_FILE")" || return 1
  # NOOP rather than deferred: the file is here and holds none of the four keys,
  # and nothing writes them any more — `config set` refuses a key neither this
  # checkout nor the installed workbench reads. A copy of the old shape can still
  # arrive by adoption, which the marker above covers by forgetting this entry.
  [[ "$legacy" != "0" ]] || return "$MIGRATION_NOOP"

  info "Moving the agent settings out of review in config.yml"

  local key
  for key in model thinking provider phases; do
    _agent_config_section_move "$key" || return 1
  done

  # The emptied review section goes too: a bare `review: {}` left behind reads as
  # a setting someone chose. A review still holding effort is kept.
  yq -i 'del(.review | select(length == 0))' "$WORKBENCH_CONFIG_FILE" || return 1

  success "config.yml now holds the agent settings under agent"
}
