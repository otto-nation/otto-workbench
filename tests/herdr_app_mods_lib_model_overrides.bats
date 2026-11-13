#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/model_overrides.js — per-run model overrides.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over model_overrides.js exports. Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/model_overrides.js');
    const { REVIEW_PHASES, BATCH_PHASES, RUNS_MODELS, overrideEntries, phaseModelFlags, withPhaseModels, overrideSummary } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "blank and whitespace values are dropped; order follows the phase list, not the map" {
  _js 'overrideEntries({group:" b ", scout:"a", synthesis:"  ", disprove:""}, REVIEW_PHASES)'
  [ "$status" -eq 0 ]
  jq -e '.==[["scout","a"],["group","b"]]' <<<"$output"
}

@test "a phase outside the list is ignored" {
  _js 'phaseModelFlags({rebase:"x", scout:"a"}, REVIEW_PHASES)'
  [ "$status" -eq 0 ]
  jq -e '.==["--phase-model","scout=a"]' <<<"$output"
}

@test "withPhaseModels puts the flags right after pr, before the subcommand" {
  _js 'withPhaseModels(["pr","review","--pr","7"], {scout:"a"}, REVIEW_PHASES)'
  [ "$status" -eq 0 ]
  jq -e '.==["pr","--phase-model","scout=a","review","--pr","7"]' <<<"$output"
}

@test "no overrides leaves the argv untouched" {
  _js 'withPhaseModels(["pr","review"], {scout:""}, REVIEW_PHASES)'
  [ "$status" -eq 0 ]
  jq -e '.==["pr","review"]' <<<"$output"
}

@test "the summary reads phase=model joined with middots" {
  _js '[overrideSummary({scout:"a",group:"b"}, REVIEW_PHASES), overrideSummary({}, REVIEW_PHASES)]'
  [ "$status" -eq 0 ]
  jq -e '.==["scout=a · group=b",""]' <<<"$output"
}

@test "every phase the picker offers is a phase pr knows" {
  _js '[...new Set([...REVIEW_PHASES, ...BATCH_PHASES])]'
  [ "$status" -eq 0 ]
  offered="$output"
  known="$(PYTHONPATH="$REPO_ROOT/ai/lib" python3 -c 'import json; from core.phases import Phase; print(json.dumps([p.value for p in Phase]))')"
  jq -en --argjson offered "$offered" --argjson known "$known" '($offered - $known) == []'
}

@test "RUNS_MODELS is exactly review/rerun/recover and has no post or dismiss" {
  _js '[[...RUNS_MODELS].sort(), ["post","dismiss"].filter(a => RUNS_MODELS.has(a))]'
  [ "$status" -eq 0 ]
  jq -e '.==[["recover","rerun","review"],[]]' <<<"$output"
}

@test "every phase in the review list is a review-domain phase" {
  _js 'REVIEW_PHASES'
  [ "$status" -eq 0 ]
  offered="$output"
  known="$(PYTHONPATH="$REPO_ROOT/ai/lib" python3 -c 'import json; from agent.registry import REVIEW_PHASES; print(json.dumps([p.value for p in REVIEW_PHASES]))')"
  jq -en --argjson offered "$offered" --argjson known "$known" '($offered - $known) == []'
}
