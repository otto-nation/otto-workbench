// Queue: this machine's otto-queue and each remote's, polled while this pane is shown. A row
// expands to its command, timings and log tail; live rows cancel after a confirm.
import { View } from "gpui-kit";
import { exec, spaces } from "herdr";
import * as herdr from "herdr";
import { eachByMachine, machineOf } from "./_lib/board_pull.js";
import { cancelEntry, loadLog, loadStatus } from "./_lib/queue.js";
import { renderQueue } from "./view.js";

function uniqueOns(values) {
  const seen = new Set();
  const out = [];
  for (const on of values) {
    if (on == null || on === "") continue;
    const key = String(on);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  out.sort();
  return out;
}

function fromSpaces() {
  return uniqueOns((spaces() ?? []).map((s) => s?.machine));
}

function remoteOns() {
  if (typeof herdr.machines !== "function") return fromSpaces();
  const list = herdr.machines();
  if (!Array.isArray(list)) return fromSpaces();
  return uniqueOns(list
    .filter((m) => m != null && m.name != null && m.layout !== "unavailable")
    .map((m) => m.name));
}

const rowKey = (on, id) => `${machineOf(on)}#${id}`;

export default class Queue extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.byOn = new Map();
    this.cancelErr = new Map();
    this.visible = false;
    this.inflight = false;
    this.again = false;
    this.manual = false;
    // The expanded row ({on, id}), its log tail, and the row whose Cancel awaits a confirm.
    this.open = null;
    this.log = null;
    this.confirming = null;
    cx.timer.every(15000, (cx) => { if (this.visible) this.refresh(cx); });
    this.refresh(cx);
  }

  // This machine first (on: null), then every reachable remote.
  targets() {
    return [{ on: null, cwd: null }, ...remoteOns().map((on) => ({ on, cwd: null }))];
  }

  refresh(cx) {
    if (this.inflight) {
      this.again = true;
      return;
    }
    this.inflight = true;
    cx.spawn(async (cx) => {
      try {
        await this.pull();
        await this.pullLog();
      } finally {
        this.inflight = false;
        this.manual = false;
        cx.notify();
        if (this.again) {
          this.again = false;
          this.refresh(cx);
        }
      }
    });
  }

  refreshNow() {
    this.manual = true;
    this.acx.notify();
    this.refresh(this.acx);
  }

  async pull() {
    const wanted = this.targets();
    const keys = new Set(wanted.map((t) => machineOf(t.on)));
    for (const key of [...this.byOn.keys()]) {
      if (!keys.has(key)) this.byOn.delete(key);
    }
    await eachByMachine(wanted, (t) => t.on, async (t) => {
      this.byOn.set(machineOf(t.on), await loadStatus(exec, { on: t.on, cwd: null }));
    });
  }

  openRow() {
    if (this.open == null) return null;
    const rows = this.byOn.get(machineOf(this.open.on))?.rows ?? [];
    return rows.find((r) => r.id === this.open.id) ?? null;
  }

  // Re-read the open row's log; a row with no log yet (queued) shows that instead.
  async pullLog() {
    const open = this.open;
    const row = this.openRow();
    if (open == null || row == null) return;
    if (!row.log_path) {
      this.log = { text: "" };
      return;
    }
    const r = await loadLog(exec, { on: open.on, cwd: null }, row.log_path);
    if (this.open !== open) return;
    this.log = r.ok ? { text: r.text } : { error: r.error?.message ?? "could not read the log" };
  }

  toggle(on, id) {
    const same = this.open != null && rowKey(this.open.on, this.open.id) === rowKey(on, id);
    this.open = same ? null : { on, id };
    this.log = null;
    this.confirming = null;
    this.acx.notify();
    if (!same) this.refresh(this.acx);
  }

  askCancel(on, id) {
    this.confirming = rowKey(on, id);
    this.acx.notify();
  }

  dismissCancel() {
    this.confirming = null;
    this.acx.notify();
  }

  cancel(on, id) {
    this.confirming = null;
    const key = machineOf(on);
    this.acx.spawn(async (cx) => {
      const r = await cancelEntry(exec, { on, cwd: null }, id);
      if (r?.ok) this.cancelErr.delete(key);
      else this.cancelErr.set(key, r?.error ?? "cancel failed");
      this.refresh(cx);
    });
  }

  sections() {
    return this.targets().map((t) => ({
      on: t.on,
      result: this.byOn.get(machineOf(t.on)),
      cancelError: this.cancelErr.get(machineOf(t.on)),
    }));
  }

  render(_cx) {
    this.visible = true;
    return renderQueue({
      sections: this.sections(),
      nowMs: Date.now(),
      openKey: this.open == null ? null : rowKey(this.open.on, this.open.id),
      confirming: this.confirming,
      log: this.log,
      manual: this.manual,
      rowKey,
      onToggle: (on, id) => this.toggle(on, id),
      onAskCancel: (on, id) => this.askCancel(on, id),
      onCancel: (on, id) => this.cancel(on, id),
      onDismiss: () => this.dismissCancel(),
      onRefresh: () => this.refreshNow(),
    });
  }
}
