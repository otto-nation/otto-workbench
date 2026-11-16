// Hosts: this machine's host-health and each remote's, polled while this pane is shown.
import { View } from "gpui-kit";
import { exec, shared, setShared, layout } from "herdr";
import { eachByMachine, machineOf } from "./_lib/board_pull.js";
import { hostsModel, toggleCollapsed } from "./_lib/hosts.js";
import { loadHealth } from "./_lib/hosts_load.js";
import { LOAD_DELAY_MS } from "./_lib/load_state.js";
import { panelOn } from "./_lib/panel_toggle.js";
import { remoteOns } from "./_lib/machines.js";
import { refreshSpinning } from "./_lib/queue_lanes.js";
import { renderHosts } from "./view.js";

export default class Hosts extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.byOn = new Map();
    this.inflight = false;
    this.again = false;
    this.refreshSince = null;
    this.by = "cpu";
    cx.timer.every(15000, (cx) => { if (panelOn(layout(), "hosts-panel")) this.refresh(cx); });
    this.refresh(cx);
  }

  // This machine first (on: null), then every reachable remote.
  targets() {
    return [{ on: null }, ...remoteOns().map((on) => ({ on }))];
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
        await this.pull();
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
    this.refresh(this.acx);
    this.acx.notify();
  }

  async pull() {
    const wanted = this.targets();
    const keys = new Set(wanted.map((t) => machineOf(t.on)));
    for (const key of [...this.byOn.keys()]) {
      if (!keys.has(key)) this.byOn.delete(key);
    }
    await eachByMachine(wanted, (t) => t.on, async (t) => {
      const key = machineOf(t.on);
      const prev = this.byOn.get(key);
      const result = await loadHealth(exec, { on: t.on, cwd: null });
      const at = result.ok ? Date.now() : prev?.at;
      const health = result.ok ? result.health : prev?.health;
      this.byOn.set(key, { result, at, health });
    });
  }

  toggleMachine(key, open) {
    setShared("hosts.collapsed", toggleCollapsed(shared("hosts.collapsed"), key, open));
    this.acx.notify();
  }

  setBy(by) {
    this.by = by;
    this.acx.notify();
  }

  model(nowMs) {
    const entries = this.targets().map((t) => {
      const key = machineOf(t.on);
      const rec = this.byOn.get(key);
      return {
        on: t.on,
        key,
        title: t.on ?? "This computer",
        result: rec?.result,
        at: rec?.at,
        health: rec?.health,
      };
    });
    return hostsModel(entries, nowMs, shared("hosts.collapsed") ?? {});
  }

  render(_cx) {
    const nowMs = Date.now();
    return renderHosts({
      blocks: this.model(nowMs),
      by: this.by,
      spinning: refreshSpinning(this.refreshSince, nowMs),
      onToggleMachine: (key, open) => this.toggleMachine(key, open),
      onBy: (by) => this.setBy(by),
      onRefresh: () => this.refreshNow(),
    });
  }
}
