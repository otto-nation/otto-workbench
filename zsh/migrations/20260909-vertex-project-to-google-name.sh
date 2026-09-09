#!/usr/bin/env bash
# Migration: rename ANTHROPIC_VERTEX_PROJECT_ID to GOOGLE_CLOUD_PROJECT in
# ~/.env.local.
#
# One GCP project is read by gcloud, the Google SDKs, Pi's built-in
# google-vertex provider and ai/lib/agent/vertex_quota.py. Only the last of
# those wants the Anthropic spelling, and ai/lib/vertex.env.yml now maps it
# there with `target:`. The neutral name is what the file carries.
#
# Deliberately not a rename of CLOUD_ML_REGION. That variable is where the
# Anthropic models are provisioned, not where Google's are served — see
# ai/lib/vertex-google.env.yml.

migration_20260909_vertex_project_to_google_name() {
  # Deferred rather than noop: a noop is recorded and never revisited, and
  # ~/.env.local is created later in this same sync by step_env_local. A machine
  # installing for the first time would record this as done against a file that
  # did not exist yet, and 20260903-vertex-exports-to-env-local can append the
  # old name to it on a later pass — which this would then never see.
  [[ -f "$ENV_LOCAL_FILE" ]] || return "$MIGRATION_DEFERRED"

  local old=ANTHROPIC_VERTEX_PROJECT_ID
  local new=GOOGLE_CLOUD_PROJECT

  grep -q "^export ${old}=" "$ENV_LOCAL_FILE" || {
    # Nothing set under the old name. The commented catalogue line the old
    # template carried is still worth renaming so the reference matches what the
    # registries now generate, but a file with neither is already in shape.
    if grep -q "^# export ${old}=" "$ENV_LOCAL_FILE"; then
      sed -i '' -e "s/^# export ${old}=/# export ${new}=/" "$ENV_LOCAL_FILE"
      return 0
    fi
    return "$MIGRATION_NOOP"
  }

  # Both names set. The rename would drop one of two values that are supposed to
  # be the same project, so it stops and says which line it left behind: an
  # automatic pick here is a silent repoint of either vertex_quota.py or every
  # gcloud call in the shell, depending on which way it guessed.
  if grep -q "^export ${new}=" "$ENV_LOCAL_FILE"; then
    warn "$ENV_LOCAL_FILE sets both ${old} and ${new} — left as they are"
    info "  ${new} is the one the workbench reads now; delete the ${old} line once they agree"
    return "$MIGRATION_NOOP"
  fi

  sed -i '' \
    -e "s/^export ${old}=/export ${new}=/" \
    -e "s/^# export ${old}=/# export ${new}=/" \
    "$ENV_LOCAL_FILE"

  success "Renamed ${old} to ${new} in $ENV_LOCAL_FILE"
  return 0
}
