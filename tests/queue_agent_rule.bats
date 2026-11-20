#!/usr/bin/env bats
# Agents route heavy shell work through otto-queue (issue 1995, option C1).
# select-on: ai/registry.yml ai/guidelines/rules/general.md ai/guidelines/rules/subagents.md

setup() {
  load 'test_helper'
  common_setup
}

@test "otto-queue is a full tool-context entry that names the agent invocation" {
  local visibility when_to_use
  visibility="$(yq -r '.tools[] | select(.name == "otto-queue") | .visibility' \
    "$REPO_ROOT/ai/registry.yml")"
  when_to_use="$(yq -r '.tools[] | select(.name == "otto-queue") | .when_to_use' \
    "$REPO_ROOT/ai/registry.yml")"

  [[ "$visibility" == "full" ]]
  [[ "$when_to_use" == *"otto-queue submit --class heavy -- CMD"* ]]
  [[ "$when_to_use" == *"otto-queue wait <id>"* ]]
  [[ "$when_to_use" == *"Do not pass \`--key\`"* ]]
  [[ "$when_to_use" == *"OTTO_QUEUE_ENTRY"* ]]
  [[ "$when_to_use" == *"Exit 75"* ]]
}

@test "generated tool context renders otto-queue as a full entry" {
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  "$REPO_ROOT/bin/local/generate-tool-context"

  grep -qF '### otto-queue' "$TOOL_CONTEXT_OUTPUT"
  grep -qF 'otto-queue submit --class heavy -- CMD' "$TOOL_CONTEXT_OUTPUT"
  grep -qF 'otto-queue wait <id>' "$TOOL_CONTEXT_OUTPUT"
}

@test "Waiting on Background Work says a harness job is not admission" {
  local section
  section="$(awk '/^## Waiting on Background Work$/{p=1;next} /^## /{p=0} p' \
    "$REPO_ROOT/ai/guidelines/rules/general.md")"

  [[ "$section" == *'job_start'* ]]
  [[ "$section" == *'run_in_background'* ]]
  [[ "$section" == *'not admission'* ]]
  [[ "$section" == *'otto-queue submit --class heavy -- CMD'* ]]
  [[ "$section" == *'otto-queue wait <id>'* ]]
}

@test "Waiting on Background Work does not bound pr with a job_start timeout" {
  local section
  section="$(awk '/^## Waiting on Background Work$/{p=1;next} /^## /{p=0} p' \
    "$REPO_ROOT/ai/guidelines/rules/general.md")"

  [[ "$section" == *'timeout_minutes: 0'* ]]
  [[ "$section" == *'pr review'* ]]
  [[ "$section" == *'pr create'* ]]
  [[ "$section" != *'generous timeout_minutes'* ]]
}

@test "Waiting on Background Work does not treat a stuck queue as --no-queue" {
  local section
  section="$(awk '/^## Waiting on Background Work$/{p=1;next} /^## /{p=0} p' \
    "$REPO_ROOT/ai/guidelines/rules/general.md")"

  [[ "$section" == *'behind an older entry'* ]]
  [[ "$section" == *'Do not `--no-queue`'* ]]
  [[ "$section" == *'stuck'* ]]
}

@test "subagents do not give pr review a generous job_start timeout" {
  local text
  text="$(cat "$REPO_ROOT/ai/guidelines/rules/subagents.md")"

  [[ "$text" == *'timeout_minutes: 0'* ]]
  [[ "$text" != *'generous timeout_minutes'* ]]
}
