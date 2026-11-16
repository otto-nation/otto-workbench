// Queue: this machine's otto-queue and each remote's, polled while this pane is shown. Home is
// one block per machine; a row expands to its command, timings and log tail; live rows cancel
// after a confirm.
import { View } from "gpui-kit";
import { exec, spaces, panes, run, shared, setShared } from "herdr";
import * as herdr from "herdr";
import { eachByMachine, machineOf } from "./_lib/board_pull.js";
import { parsePrKey } from "./_lib/launch.js";
import { LOAD_DELAY_MS } from "./_lib/load_state.js";
import { cancelEntry, loadLog, loadStatus, logNote } from "./_lib/queue.js";
import { homeModel, toggleCollapsed } from "./_lib/queue_home.js";
import {
  filterRows, lanesModel, refreshSpinning, staleness, summary,
} from "./_lib/queue_lanes.js";
import { focusSteps } from "./_lib/run_session.js";
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
    this.refreshSince = null;
    this.filter = "all";
    this.openGroups = new Set();
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
    this.refreshSince = Date.now();
    cx.timer.after(LOAD_DELAY_MS, (cx) => { if (this.refreshSince != null) cx.notify(); });
    cx.spawn(async (cx) => {
      try {
        await this.pull();
        await this.pullLog();
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

  setFilter(filter) {
    this.filter = filter;
    this.acx.notify();
  }

  toggleGroup(on, id) {
    const key = rowKey(on, id);
    if (this.openGroups.has(key)) this.openGroups.delete(key);
    else this.openGroups.add(key);
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
      const result = await loadStatus(exec, { on: t.on, cwd: null });
      if (!result.ok) result.rows = prev?.result?.rows ?? [];
      const at = result.ok && !result.busy ? Date.now() : prev?.at;
      this.byOn.set(key, { result, at });
    });
  }

  openRow() {
    if (this.open == null) return null;
    const rows = this.byOn.get(machineOf(this.open.on))?.result?.rows ?? [];
    return rows.find((r) => r.id === this.open.id) ?? null;
  }

  // Re-read the open row's log; a row with no log shows logNote instead of a tail.
  async pullLog() {
    const open = this.open;
    const row = this.openRow();
    if (open == null || row == null) return;
    if (logNote(row) != null) {
      this.log = null;
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

  toggleMachine(key) {
    setShared("queue.collapsed", toggleCollapsed(shared("queue.collapsed"), key));
    this.acx.notify();
  }

  copyPr(row) {
    const parsed = parsePrKey(row?.subject);
    if (!parsed) return;
    this.acx.write_to_clipboard(`https://github.com/${parsed.repo}/pull/${parsed.number}`);
  }

  focusTerminal(address) {
    if (address == null || address === "") return;
    for (const [name, args] of focusSteps(address, panes(), spaces())) run(name, args);
  }

  queueModel(nowMs) {
    const machines = [];
    const allRows = [];
    for (const t of this.targets()) {
      const key = machineOf(t.on);
      const entry = this.byOn.get(key);
      const result = entry?.result;
      const stale = result == null ? null : staleness({ result, at: entry.at }, nowMs);
      const rows = stale?.rows ?? result?.rows ?? [];
      allRows.push(...rows);
      machines.push({
        on: t.on, key, title: t.on ?? "This computer", at: entry?.at, result, stale, rows,
      });
    }
    const blocks = homeModel(machines, nowMs, shared("queue.collapsed") ?? {}).map((block) => {
      const src = machines.find((m) => m.key === block.key);
      const stale = src?.stale;
      const result = src?.result;
      const rows = src?.rows ?? [];
      const kind = stale?.kind;
      return {
        ...block,
        section: {
          on: src?.on ?? (block.key === "" ? null : block.key),
          label: block.summary.title,
          stale: stale && { words: stale.words, warn: kind !== "fresh" },
          muted: kind === "stale" || kind === "unreachable",
          loading: result == null,
          busy: Boolean(result?.ok && result.busy),
          lanes: result == null || (result.ok && result.busy) ? [] : lanesModel(filterRows(rows, this.filter), nowMs),
          cancelError: this.cancelErr.get(block.key),
        },
      };
    });
    return { blocks, summary: summary(allRows) };
  }

  render(_cx) {
    this.visible = true;
    const nowMs = Date.now();
    const model = this.queueModel(nowMs);
    return renderQueue({
      blocks: model.blocks,
      summary: model.summary,
      filter: this.filter,
      spinning: refreshSpinning(this.refreshSince, nowMs),
      nowMs,
      openKey: this.open == null ? null : rowKey(this.open.on, this.open.id),
      openGroups: this.openGroups,
      confirming: this.confirming,
      log: this.log,
      rowKey,
      onToggle: (on, id) => this.toggle(on, id),
      onToggleGroup: (on, id) => this.toggleGroup(on, id),
      onToggleMachine: (key) => this.toggleMachine(key),
      onOpenPr: (row) => this.copyPr(row),
      onOpenTerminal: (id) => this.focusTerminal(id),
      onAskCancel: (on, id) => this.askCancel(on, id),
      onCancel: (on, id) => this.cancel(on, id),
      onDismiss: () => this.dismissCancel(),
      onRefresh: () => this.refreshNow(),
      onFilter: (filter) => this.setFilter(filter),
    });
  }
}
