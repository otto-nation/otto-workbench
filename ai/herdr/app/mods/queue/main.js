// Queue: this machine's otto-queue and each remote's, polled while this pane is shown. Home is
// one card per machine; a click opens that machine's page of coalesced runs. A row expands to
// its command, timings and log tail; live rows cancel after a confirm.
import { View } from "gpui-kit";
import { exec, spaces, panes, run, shared, setShared } from "herdr";
import * as herdr from "herdr";
import { eachByMachine, machineOf } from "./_lib/board_pull.js";
import { parsePrKey } from "./_lib/launch.js";
import { LOAD_DELAY_MS } from "./_lib/load_state.js";
import { cancelEntry, loadLog, loadStatus, logNote } from "./_lib/queue.js";
import {
  clearMachine, homeModel, machinePage, pageOf, selectMachine,
} from "./_lib/queue_home.js";
import { refreshSpinning, staleness } from "./_lib/queue_lanes.js";
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
const groupKey = (on, row) => `${machineOf(on)}:${row.key}`;

function navState() {
  const machine = shared("queue.machine");
  if (machine == null || machine === "") {
    return machine === "" ? { machine: "" } : { machine: null };
  }
  return { machine };
}

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
    this.waitingAll = new Set();
    this.finishedAll = new Set();
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

  toggleGroup(on, row) {
    const key = groupKey(on, row);
    const map = { ...(shared("queue.groups") ?? {}) };
    map[key] = !map[key];
    setShared("queue.groups", map);
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

  openMachine(key) {
    setShared("queue.machine", selectMachine(navState(), key).machine);
    this.acx.notify();
  }

  closeMachine() {
    setShared("queue.machine", clearMachine(navState()).machine);
    this.acx.notify();
  }

  showMore(key) {
    this.waitingAll.add(key);
    this.acx.notify();
  }

  showOlder(key) {
    this.finishedAll.add(key);
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
    for (const t of this.targets()) {
      const key = machineOf(t.on);
      const entry = this.byOn.get(key);
      const result = entry?.result;
      const stale = result == null ? null : staleness({ result, at: entry.at }, nowMs);
      const rows = stale?.rows ?? result?.rows ?? [];
      machines.push({
        on: t.on, key, title: t.on ?? "This computer", at: entry?.at, result, stale, rows,
      });
    }
    const current = pageOf(navState(), machines);
    const cards = homeModel(machines, nowMs);
    if (current == null) return { cards, page: null, on: null, key: "" };
    const kind = current.stale?.kind;
    const result = current.result;
    return {
      cards,
      page: machinePage(current, nowMs, {
        allWaiting: this.waitingAll.has(current.key),
        allFinished: this.finishedAll.has(current.key),
      }),
      on: current.on,
      key: current.key,
      muted: kind === "stale" || kind === "unreachable",
      loading: result == null,
      busy: Boolean(result?.ok && result.busy),
      cancelError: this.cancelErr.get(current.key),
    };
  }

  render(_cx) {
    this.visible = true;
    const nowMs = Date.now();
    const model = this.queueModel(nowMs);
    return renderQueue({
      cards: model.cards,
      page: model.page,
      on: model.on,
      muted: model.muted,
      loading: model.loading,
      busy: model.busy,
      cancelError: model.cancelError,
      spinning: refreshSpinning(this.refreshSince, nowMs),
      nowMs,
      openKey: this.open == null ? null : rowKey(this.open.on, this.open.id),
      openGroups: shared("queue.groups") ?? {},
      confirming: this.confirming,
      log: this.log,
      rowKey,
      groupKey,
      onToggle: (on, id) => this.toggle(on, id),
      onToggleGroup: (on, row) => this.toggleGroup(on, row),
      onSelectMachine: (key) => this.openMachine(key),
      onBack: () => this.closeMachine(),
      onShowMore: () => this.showMore(model.key),
      onShowOlder: () => this.showOlder(model.key),
      onOpenPr: (row) => this.copyPr(row),
      onOpenTerminal: (id) => this.focusTerminal(id),
      onAskCancel: (on, id) => this.askCancel(on, id),
      onCancel: (on, id) => this.cancel(on, id),
      onDismiss: () => this.dismissCancel(),
      onRefresh: () => this.refreshNow(),
    });
  }
}
