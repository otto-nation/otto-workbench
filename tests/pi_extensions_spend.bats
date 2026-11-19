#!/usr/bin/env bats
# Tests for the per-model spend rollup shared by Auto's footer and session-brief.
# select-on: ai/pi/extensions/_shared/spend.ts
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _spend BODY — runs BODY with m (spend.ts) and out(v) in scope.
# BODY must not contain dollar-brace or backticks.
_spend() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/_shared/spend.ts');
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

@test "formatCost: zero, sub-mill, sub-cent, and cents" {
  _spend 'out([m.formatCost(0), m.formatCost(-1), m.formatCost(0.0004), m.formatCost(0.004), m.formatCost(0.04), m.formatCost(12.4)])'
  [ "$status" -eq 0 ]
  [ "$output" = '["$0.00","$0.00","<$0.001","$0.004","$0.04","$12.40"]' ]
}

@test "shortModelLabel: known families, carbon not gemini, else first segment" {
  _spend 'out([m.shortModelLabel("claude-opus-5-5"), m.shortModelLabel("google-vertex-claude/claude-sonnet-5-5"),
    m.shortModelLabel("claude-haiku-4-5"), m.shortModelLabel("grok-4.6"), m.shortModelLabel("gemini-3.8-flash"),
    m.shortModelLabel("gemini-4-carbon"), m.shortModelLabel("google-vertex/gemini-4-carbon"),
    m.shortModelLabel("provider/other-id")])'
  [ "$status" -eq 0 ]
  [ "$output" = '["opus","sonnet","haiku","grok","gemini","carbon","carbon","other"]' ]
}

@test "spendFromEntries: sums by label, drops zero, highest first" {
  _spend '
    const entries = [
      { message: { role: "assistant", model: "claude-opus-5-5", usage: { cost: { total: 10 } } } },
      { message: { role: "assistant", model: "grok-4.6", usage: { cost: { total: 0.01 } } } },
      { message: { role: "assistant", model: "claude-opus-5-5", usage: { cost: { total: 2.4 } } } },
      { message: { role: "assistant", model: "gemini-3.8-flash", usage: { cost: { total: 0 } } } },
      { type: "message", message: { role: "user", content: "hi" } },
    ];
    out(m.spendFromEntries(entries));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"label":"opus","cost":12.4},{"label":"grok","cost":0.01}]' ]
}

@test "spendFromEntries: numeric cost, responseModel, tool-result usage, top-level usage" {
  _spend '
    const entries = [
      { message: { responseModel: "claude-opus-5-5", usage: { cost: 1.5 } } },
      { type: "message", message: { role: "toolResult", usage: { cost: { total: 0.5 } } }, modelId: "claude-opus-5-5" },
      { usage: { cost: { total: 0.25 } }, modelId: "grok-4.6" },
    ];
    out(m.spendFromEntries(entries));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"label":"opus","cost":2},{"label":"grok","cost":0.25}]' ]
}

@test "spendFromEntries: subagent details.results by each result model" {
  _spend '
    const entries = [
      { message: { role: "assistant", model: "claude-sonnet-5-5", usage: { cost: { total: 1 } } } },
      { message: { role: "toolResult", details: { mode: "parallel", results: [
        { usage: { cost: 3 }, model: "google-vertex/gemini-4-carbon" },
        { usage: { cost: 0.5 }, model: "grok-4.6" },
        { usage: { cost: 0 }, model: "claude-sonnet-5-5" },
      ] } } },
    ];
    out(m.spendFromEntries(entries));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"label":"carbon","cost":3},{"label":"sonnet","cost":1},{"label":"grok","cost":0.5}]' ]
}

@test "spendFromEntries: details.results skip parent toolResult usage" {
  _spend '
    const entries = [
      { message: { role: "toolResult", usage: { cost: { total: 99 } }, modelId: "claude-opus-5-5",
        details: { mode: "single", results: [{ usage: { cost: 1.2 }, model: "grok-4.6" }] } } },
    ];
    out(m.spendFromEntries(entries));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"label":"grok","cost":1.2}]' ]
}

@test "formatSpend: labelled costs joined; empty is empty" {
  _spend 'out([m.formatSpend([{ label: "opus", cost: 12.4 }, { label: "grok", cost: 0.01 }]), m.formatSpend([])])'
  [ "$status" -eq 0 ]
  [ "$output" = '["opus $12.40 · grok $0.01",""]' ]
}

@test "lastPhysicalId: the newest entry that named a model" {
  _spend '
    out([m.lastPhysicalId([
      { message: { role: "user" } },
      { message: { role: "assistant", model: "claude-opus-5-5" } },
      { message: { role: "assistant", responseModel: "grok-4.6" } },
    ]), m.lastPhysicalId([{ message: { role: "user" } }])]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["grok-4.6",null]' ]
}

@test "lastPhysicalId: ignores a bare top-level model (subagent result shape)" {
  _spend '
    out([m.lastPhysicalId([{ model: "sub-model" }])]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[null]' ]
}

@test "footerLines: auto, then the strip; physical id when known; empty spend is \$0.00" {
  _spend '
    out([m.footerLines({ physicalId: "claude-sonnet-5-5", spend: [{ label: "sonnet", cost: 1 }] }),
      m.footerLines({ spend: [] }),
      m.footerLines({ physicalId: "claude-opus-5-5", spend: [] })]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["auto → claude-sonnet-5-5","sonnet $1.00"],["auto","$0.00"],["auto → claude-opus-5-5","$0.00"]]' ]
}
