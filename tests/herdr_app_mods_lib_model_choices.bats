#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/model_choices.js — picker sections from pr models --json.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# $1 = JS expression over model_choices.js exports. Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/model_choices.js');
    const doc = {
      schema: 1,
      phases: {
        synthesis: {
          domain: 'review', tier: 'deep',
          default: { ref: 'anthropic/claude-opus-5', source_key: 'models.deep' },
          review_floor: true, error: null,
        },
        rebase: {
          domain: 'rebase', tier: 'standard',
          default: { ref: 'anthropic/claude-sonnet-5', source_key: 'models.standard' },
          review_floor: false, error: null,
        },
      },
      tiers: {
        deep: { ref: 'anthropic/claude-opus-5', review_ok: true },
        standard: { ref: 'anthropic/claude-sonnet-5', review_ok: true },
        light: { ref: 'anthropic/claude-haiku-5', review_ok: false },
      },
      enabled: ['anthropic/claude-opus-5', 'anthropic/claude-haiku-5'],
      catalogue: {
        ok: true, error: null,
        providers: [
          { provider: 'anthropic', models: [
            { id: 'claude-opus-5', ref: 'anthropic/claude-opus-5', context: 200000, review_ok: true },
            { id: 'claude-haiku-5', ref: 'anthropic/claude-haiku-5', context: 200000, review_ok: false },
          ]},
          { provider: 'openai', models: [
            { id: 'gpt-5', ref: 'openai/gpt-5', context: 128000, review_ok: true },
          ]},
        ],
      },
    };
    const { choicesArgv, parseChoices, phaseOptions, defaultLabel, SECTION_CONFIGURED, SECTION_TIERS, SECTION_ENABLED, FLOOR_REASON } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "choicesArgv is pr models --json, with --repo-dir when given" {
  _js '[choicesArgv(), choicesArgv("/repo"), choicesArgv("  ")]'
  [ "$status" -eq 0 ]
  jq -e '.==[["pr","models","--json"],["pr","models","--json","--repo-dir","/repo"],["pr","models","--json"]]' <<<"$output"
}

@test "defaultLabel is Configured · ref when the phase has a default" {
  _js '[defaultLabel(doc, "synthesis"), defaultLabel(doc, "missing")]'
  [ "$status" -eq 0 ]
  jq -e '.==["Configured · anthropic/claude-opus-5","Configured"]' <<<"$output"
}

@test "phaseOptions sections are Configured, Tiers, Enabled in Pi, then each provider" {
  _js 'phaseOptions(doc, "synthesis", "").sections.map(s => s.label)'
  [ "$status" -eq 0 ]
  jq -e '.==["Configured","Tiers","Enabled in Pi","anthropic","openai"]' <<<"$output"
}

@test "a query keeps matching rows and drops empty sections" {
  _js 'phaseOptions(doc, "synthesis", "haiku").sections.map(s => [s.label, s.rows.map(r => r.ref)])'
  [ "$status" -eq 0 ]
  jq -e '.==[["Tiers",["anthropic/claude-haiku-5"]],["Enabled in Pi",["anthropic/claude-haiku-5"]],["anthropic",["anthropic/claude-haiku-5"]]]' <<<"$output"
}

@test "review_floor phases disable review_ok false rows with a reason; others do not" {
  _js '(() => {
    const syn = phaseOptions(doc, "synthesis", "").sections.flatMap(s => s.rows)
      .filter(r => r.ref === "anthropic/claude-haiku-5");
    const reb = phaseOptions(doc, "rebase", "").sections.flatMap(s => s.rows)
      .filter(r => r.ref === "anthropic/claude-haiku-5");
    return {
      syn: syn.map(r => [r.disabled, r.reason]),
      reb: reb.map(r => [r.disabled, r.reason]),
      reason: FLOOR_REASON,
    };
  })()'
  [ "$status" -eq 0 ]
  jq -e '.syn | length > 0 and all(.[0] == true and .[1] == $r)' --argjson r "$(jq -c '.reason' <<<"$output")" <<<"$output"
  jq -e '.reb | length > 0 and all(.[0] == false and .[1] == null)' <<<"$output"
}

@test "unknown schema is a fail-open parse" {
  _js '[parseChoices({schema: 2, phases: {}, tiers: {}, enabled: [], catalogue: {}}).ok, parseChoices({schema: 2}).error.kind, parseChoices(null).ok, phaseOptions({schema: 2}, "synthesis", "").ok]'
  [ "$status" -eq 0 ]
  jq -e '.==[false,"outdated",false,false]' <<<"$output"
}

@test "the row ref is passed verbatim, including unusual spelling" {
  _js '(() => {
    const weird = "Provider/Id With Spaces";
    const d = JSON.parse(JSON.stringify(doc));
    d.catalogue.providers.push({
      provider: "weird",
      models: [{ id: "Id With Spaces", ref: weird, context: null, review_ok: true }],
    });
    const row = phaseOptions(d, "rebase", "Spaces").sections.flatMap(s => s.rows)
      .find(r => r.label === "Id With Spaces");
    return [row.ref, row.ref === weird];
  })()'
  [ "$status" -eq 0 ]
  jq -e '.==["Provider/Id With Spaces",true]' <<<"$output"
}
