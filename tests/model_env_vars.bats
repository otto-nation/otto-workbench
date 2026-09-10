#!/usr/bin/env bats
# Tests for collect_model_env_vars — the env vars the registries declare as
# carrying model ids, which both harness syncs build their model config from.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_env_registry DIR NAME CONTENT — a *.env.yml under DIR.
_write_env_registry() {
  local dir="$1" name="$2" content="$3"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/$name.env.yml"
}

@test "collects the vars carrying a role, in declaration order" {
  _write_env_registry "$TMPDIR/comp" models 'meta:
  section: Test
  validation: none
env:
  - var: AI_MODEL
    role: model-default
  - var: AI_OPUS_MODEL
    role: model-tier
  - var: AI_HAIKU_MODEL
    role: model-tier
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 3 ]]
  [[ "${vars[0]}" == "AI_MODEL" ]]
  [[ "${vars[1]}" == "AI_OPUS_MODEL" ]]
  [[ "${vars[2]}" == "AI_HAIKU_MODEL" ]]
  [[ "${roles[0]}" == "model-default" ]]
  [[ "${roles[1]}" == "model-tier" ]]
  [[ "${roles[2]}" == "model-tier" ]]
}

@test "an entry with no role contributes nothing" {
  _write_env_registry "$TMPDIR/comp" mixed 'meta:
  section: Test
  validation: none
env:
  - var: AI_MODEL
    role: model-default
  - var: SOME_OTHER_VAR
    comment: "not a model"
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 1 ]]
  [[ "${vars[0]}" == "AI_MODEL" ]]
}

@test "a registry needs no meta flag to declare a model" {
  # Unlike claude_env, which is gated on meta.claude_env: true. A role is a
  # claim about one entry and there is no once-per-file question to force.
  _write_env_registry "$TMPDIR/comp" unflagged 'meta:
  section: Test
  validation: none
env:
  - var: AI_MODEL
    role: model-default
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 1 ]]
  [[ "${vars[0]}" == "AI_MODEL" ]]
}

@test "a claude_env registry declaring no role contributes nothing" {
  # The trap this field exists to avoid: ai/lib/vertex.env.yml is flagged for
  # the settings.json mirror, so a model list keyed on claude_env would report
  # a GCP project id as a model.
  _write_env_registry "$TMPDIR/vertex" vertex 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: GOOGLE_CLOUD_PROJECT
  - var: CLOUD_ML_REGION
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
}

@test "an unknown role value is not collected" {
  # validate-registries rejects it outright; the collector matching MODEL_ROLES
  # exactly is what keeps a value that slipped through from being read as a
  # tier by one harness and ignored by the other.
  _write_env_registry "$TMPDIR/comp" bogus 'meta:
  section: Test
  validation: none
env:
  - var: AI_MODEL
    role: model-defualt
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
}

@test "models are collected across registries" {
  _write_env_registry "$TMPDIR/one" first 'meta:
  section: Test
  validation: none
env:
  - var: AI_MODEL
    role: model-default
tools: []'
  _write_env_registry "$TMPDIR/two" second 'meta:
  section: Test
  validation: none
env:
  - var: AI_OPUS_MODEL
    role: model-tier
tools: []'

  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 2 ]]
  printf '%s\n' "${vars[@]}" | grep -qx AI_MODEL
  printf '%s\n' "${vars[@]}" | grep -qx AI_OPUS_MODEL
}

@test "a scan root with no registries yields nothing" {
  local -a vars=() roles=()
  collect_model_env_vars vars roles "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
  [[ "${#roles[@]}" -eq 0 ]]
}

@test "the shipped registries declare one default and three tiers" {
  # Reads the real tree, so adding or retiring a tier is expected to change
  # these counts — update them with the registry edit. What it is here to catch
  # is the counts changing when nobody edited a registry.
  local -a vars=() roles=()
  collect_model_env_vars vars roles "$REPO_ROOT"

  local i defaults=0 tiers=0
  for (( i=0; i<${#vars[@]}; i++ )); do
    case "${roles[i]}" in
      model-default) defaults=$(( defaults + 1 )) ;;
      model-tier)    tiers=$(( tiers + 1 )) ;;
    esac
  done
  [[ "$defaults" -eq 1 ]]
  [[ "$tiers" -eq 3 ]]

  # The Vertex vars are flagged claude_env but are not models.
  run printf '%s\n' "${vars[@]}"
  [[ "$output" != *GOOGLE_CLOUD_PROJECT* ]]
  [[ "$output" != *CLOUD_ML_REGION* ]]
}
