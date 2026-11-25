#!/usr/bin/env bats
# Tests for the Herdr Settings model and CLI helpers (pure: _lib/settings.js).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/settings.js ai/herdr/app/mods/_lib/model_choices.js ai/herdr/app/mods/_lib/board_data.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _js BODY — runs BODY with s (settings.js) and out(v) in scope.
# BODY must not contain dollar-brace or backticks: bash substitutes it once.
_js() {
  run node --input-type=module -e "
    import * as s from '$REPO_ROOT/ai/herdr/app/mods/_lib/settings.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

@test "argv builders target global otto-workbench config and ai sync" {
  _js 'out([s.configModelsArgv(), s.writeTargetArgv("session", "auto"), s.writeTargetArgv("deep", "anthropic/claude-opus-5-5:high"), s.writeTargetArgv("light", "  "), s.syncArgv()])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["config","models"],["config","set","models.session","auto"],["config","model","deep","anthropic/claude-opus-5-5:high"],null,["ai","sync"]]' ]
}

@test "writeTargetArgv refuses a value the CLI would read as a flag" {
  _js 'out([s.writeTargetArgv("deep", "--help"), s.writeTargetArgv("session", "-x")])'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,null]' ]
}

@test "parseConfigModels requires claude, pi and tiers objects" {
  _js 'out([s.parseConfigModels({claude:{},pi:{},tiers:{}}).ok, s.parseConfigModels(null).ok, s.parseConfigModels({claude:{},pi:{}}).ok])'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,false]' ]
}

@test "currentValues and displayValue detect workbench/auto, provider/id:level, and unset" {
  _js '
    const autoDoc = {
      claude: { model: "opusplan", env: {} },
      pi: { defaultModel: "auto", defaultProvider: "workbench" },
      tiers: {
        deep: { provider: "anthropic", id: "claude-opus-5-5", level: "high" },
        standard: { provider: "google-vertex", id: "gemini-3.1-pro-preview", level: null },
      },
    };
    const vals = s.currentValues(autoDoc);
    const pinned = s.currentValues({
      claude: {},
      pi: { defaultModel: "claude-opus-5-5", defaultProvider: "anthropic", defaultThinkingLevel: "xhigh" },
      tiers: {},
    });
    out([vals, s.displayValue(vals.session), s.displayValue(vals.light), pinned.session]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"session":"auto","deep":"anthropic/claude-opus-5-5:high","standard":"google-vertex/gemini-3.1-pro-preview","light":""},"Auto (workbench tiers)","Not set","anthropic/claude-opus-5-5:xhigh"]' ]
}

@test "targetOptions puts Auto first for session, enforces review floor on deep/standard only, and omits workbench/auto from Enabled" {
  _js '
    const doc = {
      schema: 1,
      phases: {},
      tiers: {
        deep: { ref: "anthropic/claude-opus-5-5", review_ok: true },
        light: { ref: "anthropic/claude-haiku-5-5", review_ok: false },
      },
      enabled: ["anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5", "workbench/auto"],
      catalogue: {
        ok: true,
        error: null,
        providers: [{
          provider: "anthropic",
          models: [
            { id: "claude-opus-5-5", ref: "anthropic/claude-opus-5-5", context: 200000, review_ok: true },
            { id: "claude-haiku-5-5", ref: "anthropic/claude-haiku-5-5", context: 200000, review_ok: false },
          ],
        }],
      },
    };
    const sess = s.targetOptions(doc, "session", "", "auto");
    const deep = s.targetOptions(doc, "deep", "haiku", "");
    const light = s.targetOptions(doc, "light", "haiku", "anthropic/claude-haiku-5-5");
    out({
      firstRow: sess.sections[0].rows[0],
      sessEnabled: sess.sections.find((x) => x.label === "Enabled in Pi").rows.map((r) => r.ref),
      deepDisabled: deep.sections.flatMap((x) => x.rows).map((r) => [r.ref, r.disabled]),
      lightDisabled: light.sections.flatMap((x) => x.rows).map((r) => [r.ref, r.disabled, r.selected]),
    });
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"firstRow":{"ref":"auto","label":"Auto (workbench tiers)","selected":true,"disabled":false,"reason":null},"sessEnabled":["anthropic/claude-opus-5-5","anthropic/claude-haiku-5-5"],"deepDisabled":[["anthropic/claude-haiku-5-5",true],["anthropic/claude-haiku-5-5",true]],"lightDisabled":[["anthropic/claude-haiku-5-5",false,true],["anthropic/claude-haiku-5-5",false,true]]}' ]
}

@test "isCustomValue is true only for a non-empty value absent from targetOptions" {
  _js '
    const doc = {
      schema: 1,
      phases: {},
      tiers: {},
      enabled: ["anthropic/claude-opus-5-5"],
      catalogue: { ok: true, error: null, providers: [] },
    };
    out([
      s.isCustomValue(doc, "session", ""),
      s.isCustomValue(doc, "session", "auto"),
      s.isCustomValue(doc, "deep", "anthropic/claude-opus-5-5"),
      s.isCustomValue(doc, "deep", "anthropic/claude-opus-5-5:high"),
    ]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[false,false,false,true]' ]
}

@test "previewModel formats Claude aliases, Pi enabledModels, and skipped notes while omitting unset fields" {
  _js '
    const full = s.previewModel({
      claude: {
        model: "opusplan",
        effortLevel: null,
        env: { ANTHROPIC_DEFAULT_OPUS_MODEL: "claude-opus-5-5" },
        skipped: ["models.light: openai/gpt-6 is not a Claude model"],
      },
      pi: {
        defaultModel: "auto",
        defaultProvider: "workbench",
        enabledModels: ["anthropic/claude-opus-5-5", "workbench/auto"],
      },
      tiers: {},
    });
    const empty = s.previewModel({ claude: { env: {}, skipped: [] }, pi: {}, tiers: {} });
    out([full, empty]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"rows":[{"key":"Claude model","value":"opusplan","mono":true},{"key":"ANTHROPIC_DEFAULT_OPUS_MODEL","value":"claude-opus-5-5","mono":true},{"key":"Pi default","value":"workbench/auto","mono":true},{"key":"Pi enabled","value":"anthropic/claude-opus-5-5, workbench/auto","mono":true}],"skipped":["models.light: openai/gpt-6 is not a Claude model"]},{"rows":[],"skipped":[]}]' ]
}

@test "syncBarModel disables when clean, saving or syncing, and shows SYNC_CAPTION when dirty" {
  _js 'out([
    s.syncBarModel({ dirty: false, saving: false, syncing: false }),
    s.syncBarModel({ dirty: true, saving: false, syncing: false }),
    s.syncBarModel({ dirty: true, saving: true, syncing: false }),
    s.syncBarModel({ dirty: true, saving: false, syncing: true }),
  ])'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"disabled":true,"reason":"No unsynced changes","status":null,"text":"No unsynced changes"},{"disabled":false,"reason":null,"status":null,"text":"Pi and Claude Code pick this up after Sync"},{"disabled":true,"reason":"Saving","status":"running","text":"Saving"},{"disabled":true,"reason":"Syncing","status":"running","text":"Syncing"}]' ]
}

@test "loadConfigModels, saveTarget and runAiSync invoke otto-workbench on this machine" {
  _js '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push([cmd, args, opts.on ?? null]);
      if (args[0] === "config" && args[1] === "models") {
        return { code: 0, stdout: JSON.stringify({ claude: { env: {} }, pi: {}, tiers: {} }), stderr: "" };
      }
      return { code: 0, stdout: "", stderr: "" };
    };
    const loaded = await s.loadConfigModels(exec);
    const saved = await s.saveTarget(exec, "session", "auto");
    const synced = await s.runAiSync(exec);
    out([loaded.ok, saved.ok, synced.ok, calls]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true,[["otto-workbench",["config","models"],null],["otto-workbench",["config","set","models.session","auto"],null],["otto-workbench",["ai","sync"],null]]]' ]
}
