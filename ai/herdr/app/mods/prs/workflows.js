// Workflows: `pr batch` runs on every machine the board reads, shown from the Batch button and a PR's Batch tab.
import { exec, watchFolder } from "herdr";
import {
  listArgv, statusArgv, reportArgv, logTailArgv, parseRunList, parseRunDoc, parseSessions,
  stateChanged, laneOf,
} from "./_lib/batch.js";
import { cancelArgv, runProgress } from "./_lib/batch_launch.js";
import { invoke, readJson } from "./_lib/board_data.js";

function k(on) { return String(on ?? ""); }

function itemIn(doc, want) {
  return doc?.items?.find((i) => String(i.key ?? "").toLowerCase() === want) ?? null;
}

function newest(a, b) {
  return String(b ?? "").localeCompare(String(a ?? ""));
}

async function prParse(ctx, argv, parse) {
  const ran = await invoke(exec, ctx, "pr", argv);
  if (!ran.ok) return ran;
  const body = readJson(ran.stdout);
  return body.ok ? parse(body.value) : body;
}

export class Workflows {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  constructor(cx, machinesOf) {
    this.acx = cx;
    this.machinesOf = machinesOf;
    this.machines = new Map();
    this.keys = null;
    this.visible = false;
    this.pinned = null;
    this.openKey = this.step = this.log = null;
    this.sessionStamp = null;
    this.sessionMap = null;
    this.sessionLoading = false;
    this.sessionError = null;
    this.onRun = null;
    this.onTerminal = null;
    // The log path being read, so a slow tail is not doubled by the 3 s timer; another step's
    // click still starts its own read.
    this.logInflight = null;
    this.watching = new Set();
    cx.timer.every(15000, (cx) => { if (this.visible) this.refresh(cx); });
    cx.timer.every(3000, (cx) => {
      if (this.visible && this.openKey && this.step && this.stepStatus() === "running") this.loadLog(cx);
    });
  }

  targets() {
    const wanted = new Map();
    for (const t of this.machinesOf() ?? []) {
      const key = k(t?.on);
      if (!wanted.has(key)) wanted.set(key, { on: t.on ?? null, cwd: t.cwd });
    }
    return wanted;
  }

  show() {
    this.visible = true;
    const keys = [...this.targets().keys()].sort().join("\0");
    if (this.keys == null || this.keys !== keys) {
      this.keys = keys;
      this.refresh(this.acx);
    }
  }

  hide() { this.visible = false; }

  refresh(cx) {
    const wanted = this.targets();
    for (const key of [...this.machines.keys()]) {
      if (!wanted.has(key)) this.machines.delete(key);
    }
    if (this.pinned && !wanted.has(k(this.pinned.on))) this.pinned = null;
    if (!wanted.size) cx.notify();
    for (const [key, ctx] of wanted) this.refreshOne(cx, key, ctx);
  }

  refreshOne(cx, key, ctx) {
    let m = this.machines.get(key);
    if (!m) this.machines.set(key, m = {
      ctx, runs: [], docs: new Map(), error: null, inflight: false, again: false, loaded: false,
    });
    if (m.inflight) { m.again = true; return; }
    m.inflight = true;
    cx.spawn(async (cx) => {
      try { await this.pullOne(cx, key, m); }
      finally {
        m.inflight = false;
        m.loaded = true;
        if (this.machines.get(key) !== m) return;
        cx.notify();
        if (m.again) { m.again = false; this.refreshOne(cx, key, m.ctx); }
      }
    });
  }

  wantedIds(m) {
    const ids = new Set();
    const seen = new Set();
    for (const r of m.runs) {
      if (r.active) ids.add(r.id);
      for (const slug of r.repos ?? []) {
        if (seen.has(slug)) continue;
        seen.add(slug);
        ids.add(r.id);
      }
    }
    if (this.pinned && k(this.pinned.on) === k(m.ctx.on)) ids.add(this.pinned.id);
    return ids;
  }

  async pullOne(cx, key, m) {
    const parsed = await prParse(m.ctx, listArgv(), parseRunList);
    if (this.machines.get(key) !== m) return;
    if (!parsed.ok) { m.error = { message: parsed.error.message }; return; }
    m.runs = parsed.runs.map((r) => ({ ...r, on: m.ctx.on }));
    const next = new Map();
    let err = null;
    await Promise.all([...this.wantedIds(m)].map(async (id) => {
      const doc = await prParse(m.ctx, statusArgv(id), parseRunDoc);
      if (doc.ok) { next.set(id, doc); return; }
      err = { message: doc.error.message };
      if (m.docs.has(id)) next.set(id, m.docs.get(id));
    }));
    if (this.machines.get(key) !== m) return;
    m.docs = next;
    m.error = err;
    this.ensureSession(cx);
    if (m.ctx.on != null) return;
    for (const r of m.runs) {
      if (r.active && r.runDir) this.startWatch(cx, r.runDir);
    }
  }

  localActive(runDir) {
    const m = this.machines.get("");
    return Boolean(m?.runs.some((r) => r.active && r.runDir === runDir));
  }

  startWatch(cx, runDir) {
    if (!runDir || this.watching.has(runDir)) return;
    this.watching.add(runDir);
    cx.spawn(async (cx) => {
      try {
        while (this.watching.has(runDir) && this.localActive(runDir)) {
          const paths = await watchFolder(runDir);
          if (!this.localActive(runDir)) break;
          if (!Array.isArray(paths) || stateChanged(paths, runDir)) {
            const local = this.machines.get("");
            if (local) this.refreshOne(cx, "", local.ctx);
          }
        }
      } catch {
        // watchFolder rejected; the next pull restarts the loop if the run is still active.
      }
      this.watching.delete(runDir);
    });
  }

  docFor(slug, number) {
    const want = `${slug}#${number}`.toLowerCase();
    const hits = [];
    for (const m of this.machines.values()) {
      for (const [id, doc] of m.docs) {
        const item = itemIn(doc, want);
        if (!item) continue;
        const run = m.runs.find((r) => r.id === id) ?? {
          id, on: m.ctx.on, active: Boolean(doc.run?.active), startedAt: doc.run?.startedAt ?? null,
        };
        hits.push({ on: m.ctx.on, run, doc, item });
      }
    }
    if (!hits.length) return null;
    if (this.pinned) {
      const hit = hits.find((h) => k(h.on) === k(this.pinned.on) && h.run.id === this.pinned.id);
      if (hit) return hit;
    }
    const active = hits.filter((h) => h.run.active);
    const pool = active.length ? active : hits;
    pool.sort((a, b) => newest(a.run.startedAt, b.run.startedAt));
    return pool[0];
  }

  itemFor(slug, number) { return this.docFor(slug, number)?.item ?? null; }

  ownerOf(key) {
    if (!key) return null;
    const i = String(key).lastIndexOf("#");
    return i < 1 ? null : this.docFor(key.slice(0, i), key.slice(i + 1));
  }

  allRuns() {
    const out = [];
    for (const m of this.machines.values()) out.push(...m.runs);
    out.sort((a, b) => newest(a.startedAt, b.startedAt));
    return out;
  }

  activeRuns() {
    const out = [];
    for (const m of this.machines.values()) {
      for (const run of m.runs) {
        if (!run.active) continue;
        const doc = m.docs.get(run.id) ?? null;
        out.push({ on: run.on, run, doc, progress: runProgress(doc) });
      }
    }
    out.sort((a, b) => newest(a.run.startedAt, b.run.startedAt));
    return out;
  }

  setPin(next) {
    const same = next == null ? this.pinned == null
      : this.pinned && k(this.pinned.on) === k(next.on) && this.pinned.id === next.id;
    if (same) return this.acx.notify();
    this.pinned = next;
    this.openKey = this.step = this.log = null;
    this.refresh(this.acx);
    this.acx.notify();
  }

  pin(on, id) { this.setPin({ on, id }); }
  unpin() { this.setPin(null); }

  // A run row toggles: picking the pinned run again unpins it.
  pickRun(run) {
    const pin = this.pinned;
    if (run == null || (pin && k(pin.on) === k(run.on) && pin.id === run.id)) return this.unpin();
    this.pin(run.on, run.id);
  }

  docOf(on, id) {
    return this.machines.get(k(on))?.docs.get(id) ?? null;
  }

  async cancel(on, runId, { kill } = {}) {
    const ctx = this.machines.get(k(on))?.ctx ?? { on, cwd: null };
    const ran = await invoke(exec, ctx, "pr", cancelArgv(runId, { kill }));
    this.refresh(this.acx);
    return { ok: ran.ok, message: ran.ok ? (ran.stdout ?? "") : ran.error.message };
  }

  barState() {
    const runs = this.allRuns();
    const pin = this.pinned;
    const run = (pin && runs.find((r) => k(r.on) === k(pin.on) && r.id === pin.id)) || runs[0] || null;
    const m = run ? this.machines.get(k(run.on)) : null;
    const loaded = [...this.machines.values()].some((x) => x.loaded);
    let error = null;
    for (const t of this.machinesOf() ?? []) {
      const e = this.machines.get(k(t.on))?.error;
      if (e) { error = e; break; }
    }
    return {
      on: run?.on ?? null,
      runs,
      runId: this.pinned?.id ?? null,
      pinnedOn: this.pinned?.on ?? null,
      doc: (run && m && m.docs.get(run.id)) || null,
      error,
      loading: this.keys == null || (this.machines.size > 0 && !loaded),
      nowMs: Date.now(),
      onPickRun: (r) => this.pickRun(r),
    };
  }

  sessionOf(key) {
    return this.sessionMap?.get(String(key ?? "").toLowerCase()) ?? null;
  }

  tabState() {
    const hit = this.ownerOf(this.openKey);
    const local = hit != null && hit.on == null;
    return {
      step: this.step,
      log: this.log,
      nowMs: Date.now(),
      onStep: (s) => this.selectStep(s),
      doc: hit?.doc ?? null,
      session: this.sessionOf(this.openKey),
      sessionLoading: this.sessionLoading,
      sessionError: this.sessionError,
      onRun: this.onRun,
      onTerminal: local ? this.onTerminal : null,
    };
  }

  ensureSession(cx) {
    const key = this.openKey;
    const hit = key ? this.ownerOf(key) : null;
    if (!hit || !hit.run?.id || laneOf(hit.item) !== "attention") {
      this.sessionStamp = null;
      this.sessionMap = null;
      this.sessionLoading = false;
      this.sessionError = null;
      return;
    }
    const stamp = `${k(hit.on)}\0${hit.run.id}\0${key}\0${hit.item.status}\0${(hit.item.findings ?? []).length}`;
    if (this.sessionStamp === stamp) return;
    this.sessionStamp = stamp;
    this.sessionLoading = true;
    this.sessionError = null;
    this.sessionMap = null;
    const ctx = this.machines.get(k(hit.on))?.ctx ?? { on: hit.on, cwd: null };
    const runId = hit.run.id;
    cx.spawn(async (cx) => {
      const parsed = await prParse(ctx, reportArgv(runId), parseSessions);
      if (this.sessionStamp !== stamp) return;
      this.sessionLoading = false;
      if (!parsed.ok) {
        this.sessionError = parsed.error?.message ?? "Could not read session";
        this.sessionMap = null;
      } else {
        this.sessionError = null;
        this.sessionMap = parsed.sessions;
      }
      cx.notify();
    });
  }

  stepStatus() {
    return this.openKey && this.step
      ? (this.ownerOf(this.openKey)?.item?.steps?.[this.step]?.status ?? null)
      : null;
  }

  openItem(key) {
    if (this.openKey === key) {
      this.ensureSession(this.acx);
      return;
    }
    this.openKey = key;
    this.step = this.log = null;
    this.ensureSession(this.acx);
    this.acx.notify();
  }

  selectStep(step) {
    if (this.step === step) {
      this.step = this.log = null;
      this.acx.notify();
      return;
    }
    this.step = step;
    this.log = null;
    this.loadLog(this.acx);
    this.acx.notify();
  }

  loadLog(cx) {
    const key = this.openKey;
    const step = this.step;
    if (!key || !step) return;
    const hit = this.ownerOf(key);
    const path = hit?.item?.steps?.[step]?.logPath;
    if (!path) {
      this.log = { error: "No log for this step" };
      cx.notify();
      return;
    }
    if (this.logInflight === path) return;
    const argv = logTailArgv(path);
    const ctx = this.machines.get(k(hit.on))?.ctx ?? { on: hit.on, cwd: null };
    this.logInflight = path;
    cx.spawn(async (cx) => {
      let ran;
      try { ran = await invoke(exec, ctx, argv[0], argv.slice(1)); }
      finally { if (this.logInflight === path) this.logInflight = null; }
      if (this.openKey !== key || this.step !== step) return;
      this.log = ran.ok ? { text: ran.stdout ?? "" } : { error: ran.error.message };
      cx.notify();
    });
  }
}
