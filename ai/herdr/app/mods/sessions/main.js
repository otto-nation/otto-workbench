// Sessions: pi and Claude Code sessions on this machine and each remote — title, TL;DR, PRs, live RSS and idle.
// No polling (T1-a): it loads on mount, when the panel comes on screen, on Refresh, and after an
// action. Python owns every fact; _lib/sessions.js words and orders them.
import { View } from "gpui-kit";
import { InputState } from "gpui-base";
import { exec, run, panes, spaces, terminals, layout, shared, setShared } from "herdr";
import { eachByMachine, machineOf } from "./_lib/board_pull.js";
import { LOAD_DELAY_MS } from "./_lib/load_state.js";
import { clearMachine, navStateOf, pageOf, selectMachine } from "./_lib/machine_nav.js";
import { panelOn } from "./_lib/panel_toggle.js";
import { remoteOns } from "./_lib/machines.js";
import { refreshSpinning } from "./_lib/queue_lanes.js";
import {
  SINCE_STEP_DAYS, ensureOpenState, homeCards, isFolderOpen, loadSessions, machineSessions,
  toggleFolder as foldToggle,
} from "./_lib/sessions.js";
import { closePiSession, openPiSession } from "./_lib/sessions_open.js";
import { renderSessions } from "./view.js";

const PANEL = "sessions-panel";
const WATCH_MS = 1000;
const navState = () => navStateOf(shared("sessions.machine"));

export default class Sessions extends View {
  // Handlers run after the render that built them, so they use the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.byOn = new Map();
    this.sinceByOn = new Map();
    this.notes = new Map();
    this.renaming = null;
    this.renameInput = InputState.new({ placeholder: "Session name" });
    this.inflight = false;
    this.again = false;
    this.refreshSince = null;
    this.shown = panelOn(layout(), PANEL);
    // Not a poll: it spawns nothing and reads only the layout, so the panel loads once each time
    // it comes on screen. herdr re-runs no render on a layout click (mods/AGENTS.md § Full screen).
    cx.timer.every(WATCH_MS, (cx) => {
      const shown = panelOn(layout(), PANEL);
      if (shown && !this.shown) this.refresh(cx);
      this.shown = shown;
    });
    this.refresh(cx);
  }

  // This machine first (on: null), then every reachable remote.
  targets() {
    return [{ on: null }, ...remoteOns().map((on) => ({ on }))];
  }

  sinceOf(key) {
    return this.sinceByOn.get(key) ?? SINCE_STEP_DAYS;
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
      this.byOn.set(key, await loadSessions(exec, { on: t.on, cwd: null }, this.sinceOf(key)));
    });
  }

  // "Show older": widen that machine's window by another step and load it, on demand only (T3-e).
  older(key) {
    this.sinceByOn.set(key, this.sinceOf(key) + SINCE_STEP_DAYS);
    this.refreshNow();
  }

  openMachine(key) {
    setShared("sessions.machine", selectMachine(navState(), key).machine);
    setShared("sessions.open", null);
    this.acx.notify();
  }

  back() {
    setShared("sessions.machine", clearMachine(navState()).machine);
    this.acx.notify();
  }

  toggleFolder(key, groupCount, allKeys) {
    const next = foldToggle(shared("sessions.open"), key, groupCount, allKeys);
    setShared("sessions.open", next);
    this.acx.notify();
  }

  deps(cx) {
    return { exec, run, panes, spaces, terminals, sleep: (ms) => cx.sleep(ms) };
  }

  // Run one row action, keep its refusal beside the row, then reload what changed.
  act(row, fn) {
    this.notes.delete(row.key);
    this.acx.spawn(async (cx) => {
      let res;
      try {
        res = await fn(cx);
      } catch (err) {
        res = { ok: false, message: String(err?.message ?? err) };
      }
      if (!res.ok) this.notes.set(row.key, res.message);
      cx.notify();
      this.refresh(cx);
    });
    this.acx.notify();
  }

  open(row) {
    this.act(row, (cx) => openPiSession(this.deps(cx), row));
  }

  close(row) {
    this.act(row, () => closePiSession(exec, row));
  }

  renameStart(row) {
    this.renaming = row.key;
    this.renameInput.set_value("");
    this.acx.notify();
  }

  renameCancel() {
    this.renaming = null;
    this.acx.notify();
  }

  renameSave(row) {
    const name = String(this.renameInput.value() ?? "").trim();
    if (!name) return;
    this.renaming = null;
    this.act(row, (cx) => openPiSession(this.deps(cx), row, { name }));
  }

  machines(nowMs) {
    return this.targets().map((t) => {
      const key = machineOf(t.on);
      return machineSessions({
        key, on: t.on, title: t.on ?? "This computer", result: this.byOn.get(key), since: this.sinceOf(key),
      }, nowMs);
    });
  }

  render(_cx) {
    const nowMs = Date.now();
    const machines = this.machines(nowMs);
    const page = pageOf(navState(), machines);
    const cards = homeCards(machines);
    const groups = page?.groups ?? [];
    const allKeys = groups.map((g) => g.key);
    const open = ensureOpenState(shared("sessions.open"), allKeys);
    if (open != null && shared("sessions.open") == null) setShared("sessions.open", open);
    return renderSessions({
      cards,
      page,
      spinning: refreshSpinning(this.refreshSince, nowMs),
      renaming: this.renaming,
      renameInput: this.renameInput,
      noteOf: (key) => this.notes.get(key) ?? null,
      isOpen: (key) => isFolderOpen(open ?? shared("sessions.open"), key, groups.length),
      onSelectMachine: (key) => this.openMachine(key),
      onBack: () => this.back(),
      onToggleFolder: (key) => this.toggleFolder(key, groups.length, allKeys),
      onRefresh: () => this.refreshNow(),
      onOlder: (key) => this.older(key),
      onOpen: (row) => this.open(row),
      onClose: (row) => this.close(row),
      onRenameStart: (row) => this.renameStart(row),
      onRenameSave: (row) => this.renameSave(row),
      onRenameCancel: () => this.renameCancel(),
    });
  }
}
