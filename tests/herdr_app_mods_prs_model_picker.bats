#!/usr/bin/env bats
# Models picker: option rows in an expanded phase must be distinct click targets.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/prs/model_picker.js ai/herdr/app/mods/_lib/model_choices.js ai/herdr/app/mods/_ui/ui.js tests/herdr_app_host_helper.bash

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_app_host_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  _host_setup
}
teardown() { common_teardown; }

PICKER="$(cat <<'PICKER_JS'
const doc = {
  schema: 1,
  phases: {
    synthesis: {
      domain: "review", tier: "deep",
      default: { ref: "claude-sonnet-5-5", source_key: "models.standard" },
      review_floor: true, error: null,
    },
  },
  tiers: {
    deep: { ref: "claude-opus-5-5", review_ok: true },
    standard: { ref: "claude-sonnet-5-5", review_ok: true },
    light: { ref: "claude-haiku-4-5", review_ok: false },
  },
  enabled: ["claude-opus-5-5", "claude-sonnet-5-5"],
  catalogue: {
    ok: true, error: null,
    providers: [{
      provider: "google-vertex",
      models: [
        { id: "gemini-2.5-flash", ref: "google-vertex/gemini-2.5-flash", context: 1_000_000, review_ok: true },
        { id: "gemini-4-carbon", ref: "google-vertex/gemini-4-carbon", context: 1_000_000, review_ok: true },
      ],
    }],
  },
};
const { ModelPicker } = await import(M + "/model_picker.js");
const { phaseOptions } = await import(M + "/_lib/model_choices.js");
const blank = { value: () => "", set_value: () => {} };
const draw = () => {
  const picker = new ModelPicker(fakeCx().cx, ["synthesis"], "review-models", null);
  picker.fields.set("synthesis", blank);
  picker.search = blank;
  picker.result = { ok: true, doc };
  picker.expanded = "synthesis";
  const recs = (globalThis.__gpuiRecord = []);
  picker.content();
  const optionRows = recs.filter((r) => typeof r.click === "function" && /^review-models-synthesis-\d+$/.test(String(r.id ?? "")));
  const enabled = phaseOptions(doc, "synthesis", "").sections.flatMap((s) => s.rows).filter((r) => !r.disabled);
  const list = recs.find((r) => r.id === "review-models-synthesis-list");
  return {
    optionIds: optionRows.map((r) => r.id),
    unique: new Set(optionRows.map((r) => r.id)).size,
    enabled: enabled.length,
    refs: enabled.map((r) => r.ref),
    mousedown: optionRows.filter((r) => typeof r.mousedown === "function").length,
    listKids: list?.children?.length ?? 0,
  };
};
PICKER_JS
)"

@test "an expanded phase's option rows have unique ids across sections" {
  _host "$PICKER
    out(draw());"
  [ "$status" -eq 0 ]
  jq -e '.unique == (.optionIds | length) and .unique == .enabled and .unique > 1' <<<"$output"
  jq -e '.refs | index("google-vertex/gemini-4-carbon") != null' <<<"$output"
}

@test "hoverRow stops a left mouse_down so a popover option click is not an outside dismiss" {
  _host "$PICKER
    out(draw());"
  [ "$status" -eq 0 ]
  jq -e '.mousedown == .unique and .mousedown > 1' <<<"$output"
}

@test "an expanded phase's model list is one overflow child so it can clip" {
  _host "$PICKER
    out(draw());"
  [ "$status" -eq 0 ]
  jq -e '.listKids == 1' <<<"$output"
}
