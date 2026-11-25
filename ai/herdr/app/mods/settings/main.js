// Settings: global models.session and models.{deep,standard,light} over `otto-workbench config`,
// plus explicit `otto-workbench ai sync`. This machine only; no polling (loads on mount, when
// the panel comes on screen, and on Refresh).
import { View } from "gpui-kit";
import { InputState } from "gpui-base";
import { exec, layout } from "herdr";
import { LOAD_DELAY_MS } from "./_lib/load_state.js";
import { panelOn } from "./_lib/panel_toggle.js";
import { refreshSpinning } from "./_lib/queue_lanes.js";
import {
  SETTINGS_TARGETS, loadConfigModels, loadModelChoices, runAiSync, saveTarget,
} from "./_lib/settings.js";
import { renderSettings } from "./view.js";

const PANEL = "settings-panel";
const WATCH_MS = 1000;

export default class Settings extends View {
  // Handlers run after the render that built them, so they use the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.models = null;
    this.choices = null;
    this.choicesLoading = false;
    this.openTarget = null;
    this.customOpen = null;
    this.query = "";
    this.dirty = false;
    this.saving = false;
    this.syncing = false;
    this.note = null;
    this.inflight = false;
    this.again = false;
    this.refreshSince = null;
    this.shown = panelOn(layout(), PANEL);

    this.search = InputState.new({ placeholder: "Search" });
    this.search.on("change", () => {
      this.query = this.search.value();
      this.acx.notify();
    });
    this.customInputs = new Map(SETTINGS_TARGETS.map((target) => {
      const state = InputState.new({ placeholder: "provider/model[:level]" });
      state.on("change", () => this.acx.notify());
      return [target, state];
    }));

    // Not a poll: it spawns nothing and reads only the layout, so the panel loads once each time
    // it comes on screen.
    cx.timer.every(WATCH_MS, (cx) => {
      const shown = panelOn(layout(), PANEL);
      if (shown && !this.shown) this.refresh(cx);
      this.shown = shown;
    });
    this.refresh(cx);
  }

  refresh(cx) {
    if (this.inflight) {
      this.again = true;
      return;
    }
    this.inflight = true;
    this.refreshSince = Date.now();
    cx.timer.after(LOAD_DELAY_MS, (cx) => { if (this.refreshSince != null) cx.notify(); });
    cx.spawn(async (cx) => {
      try {
        const [models, choices] = await Promise.all([
          loadConfigModels(exec),
          loadModelChoices(exec),
        ]);
        this.models = models;
        this.choices = choices;
      } finally {
        this.inflight = false;
        this.refreshSince = null;
        cx.notify();
        if (this.again) {
          this.again = false;
          this.refresh(cx);
        }
      }
    });
  }

  refreshNow() {
    this.note = null;
    this.refresh(this.acx);
    this.acx.notify();
  }

  toggleTarget(target, open, current) {
    if (open) {
      this.openTarget = target;
      this.customOpen = null;
      this.query = "";
      this.search.set_value("");
      this.customInputs.get(target)?.set_value(current || "");
      if (this.choices == null && !this.choicesLoading) this.pullChoices();
    } else if (this.openTarget === target) {
      this.openTarget = null;
      this.customOpen = null;
    }
    this.acx.notify();
  }

  pullChoices() {
    this.choicesLoading = true;
    this.acx.spawn(async (cx) => {
      try {
        this.choices = await loadModelChoices(exec);
      } finally {
        this.choicesLoading = false;
        cx.notify();
      }
    });
  }

  openCustom(target) {
    this.customOpen = target;
    this.acx.notify();
  }

  writeValue(target, value) {
    if (this.saving || this.syncing) return;
    this.saving = true;
    this.note = null;
    this.openTarget = null;
    this.customOpen = null;
    this.acx.notify();
    this.acx.spawn(async (cx) => {
      try {
        const saved = await saveTarget(exec, target, value);
        if (!saved.ok) {
          this.note = saved.error?.message ?? "Could not update setting";
          return;
        }
        this.dirty = true;
        const [models, choices] = await Promise.all([
          loadConfigModels(exec),
          loadModelChoices(exec),
        ]);
        // Keep the last valid form mounted if a partial edit (e.g. Auto before all tiers name
        // a provider) makes `config models` refuse, and surface its message in the action bar.
        if (models.ok) {
          this.models = models;
        } else {
          this.note = models.error?.message ?? "Config incomplete";
        }
        if (choices.ok || this.choices == null) this.choices = choices;
      } finally {
        this.saving = false;
        cx.notify();
      }
    });
  }

  saveCustom(target) {
    const val = this.customInputs.get(target)?.value() ?? "";
    this.writeValue(target, val);
  }

  syncNow() {
    if (!this.dirty || this.saving || this.syncing) return;
    this.syncing = true;
    this.note = null;
    this.acx.notify();
    this.acx.spawn(async (cx) => {
      try {
        const ran = await runAiSync(exec);
        if (!ran.ok) {
          this.note = ran.error?.message ?? "Sync failed";
          return;
        }
        this.dirty = false;
        const models = await loadConfigModels(exec);
        if (models.ok) this.models = models;
      } finally {
        this.syncing = false;
        cx.notify();
      }
    });
  }

  render(_cx) {
    return renderSettings({
      models: this.models,
      choices: this.choices,
      openTarget: this.openTarget,
      customOpen: this.customOpen,
      query: this.query,
      search: this.search,
      customInputs: this.customInputs,
      dirty: this.dirty,
      saving: this.saving,
      syncing: this.syncing,
      busy: this.saving || this.syncing,
      note: this.note,
      spinning: refreshSpinning(this.refreshSince, Date.now()),
      onRefresh: () => this.refreshNow(),
      onToggleTarget: (target, open, current) => this.toggleTarget(target, open, current),
      onPick: (target, ref) => this.writeValue(target, ref),
      onOpenCustom: (target) => this.openCustom(target),
      onSaveCustom: (target) => this.saveCustom(target),
      onSync: () => this.syncNow(),
    });
  }
}
