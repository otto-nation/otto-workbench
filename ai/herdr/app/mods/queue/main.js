// Queue: each remote machine's otto-queue, polled while this pane is shown.
import { View } from "gpui-kit";
import { exec, spaces } from "herdr";
import * as herdr from "herdr";
import { eachByMachine } from "./_lib/board_pull.js";
import { cancelEntry, loadStatus } from "./_lib/queue.js";
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

export default class Queue extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.byOn = new Map();
    this.visible = false;
    this.inflight = false;
    this.again = false;
    cx.timer.every(15000, (cx) => { if (this.visible) this.refresh(cx); });
    this.refresh(cx);
  }

  targets() {
    return remoteOns().map((on) => ({ on, cwd: null }));
  }

  refresh(cx) {
    if (this.inflight) {
      this.again = true;
      return;
    }
    this.inflight = true;
    cx.spawn(async (cx) => {
      try { await this.pull(); }
      finally {
        this.inflight = false;
        cx.notify();
        if (this.again) {
          this.again = false;
          this.refresh(cx);
        }
      }
    });
  }

  async pull() {
    const wanted = this.targets();
    const keys = new Set(wanted.map((t) => t.on));
    for (const key of [...this.byOn.keys()]) {
      if (!keys.has(key)) this.byOn.delete(key);
    }
    await eachByMachine(wanted, (t) => t.on, async (t) => {
      this.byOn.set(t.on, await loadStatus(exec, { on: t.on, cwd: null }));
    });
  }

  cancel(on, id) {
    this.acx.spawn(async (cx) => {
      await cancelEntry(exec, { on, cwd: null }, id);
      this.refresh(cx);
    });
  }

  sections() {
    return this.targets().map((t) => ({
      on: t.on,
      result: this.byOn.get(t.on),
    }));
  }

  render(_cx) {
    this.visible = true;
    return renderQueue({
      sections: this.sections(),
      onCancel: (on, id) => this.cancel(on, id),
      nowMs: Date.now(),
    });
  }
}
