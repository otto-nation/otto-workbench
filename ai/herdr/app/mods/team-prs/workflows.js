// Workflows: a read-only list of `pr batch` runs on the focused space's machine.
import { exec, watchFolder } from "herdr";
import {
  listArgv, statusArgv, logTailArgv, parseRunList, parseRunDoc, stateChanged,
  attentionCount, STEP_ORDER,
} from "./_lib/batch.js";
import { invoke, readJson } from "./_lib/board_data.js";
import { renderWorkflows } from "./workflows_view.js";

function ctxKey(ctx) {
  return String(ctx?.on ?? "");
}

const HOT_STEP = new Set(["failed", "running", "needs_decision", "interrupted"]);

function defaultStep(item) {
  const steps = item?.steps ?? {};
  for (const name of STEP_ORDER) {
    if (HOT_STEP.has(steps[name]?.status)) return name;
  }
  let last = null;
  for (const name of STEP_ORDER) {
    if (steps[name]?.logPath) last = name;
  }
  return last;
}

export class Workflows {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  constructor(cx, ctxOf) {
    this.acx = cx;
    this.ctxOf = ctxOf;
    this.visible = false;
    this.runs = [];
    this.runId = null;
    this.doc = null;
    this.error = null;
    this.loading = true;
    this.openKey = null;
    this.step = null;
    this.log = null;
    this.filter = null;
    this.collapsed = new Set(["closed"]);
    this.inflight = this.again = false;
    // The log path being read, so a slow tail is not doubled by the 3 s timer; another step's
    // click still starts its own read.
    this.logInflight = null;
    this.watchRoot = null;
    this.key = ctxKey(this.ctxOf());
    cx.timer.every(15000, (cx) => {
      if (this.visible && this.ctxOf().on != null) this.refresh(cx);
    });
    cx.timer.every(3000, (cx) => {
      if (this.visible && this.openKey && this.step && this.stepStatus() === "running") {
        this.loadLog(cx);
      }
    });
  }

  show() {
    this.visible = true;
    const key = ctxKey(this.ctxOf());
    if (this.key !== key) {
      this.key = key;
      this.runs = [];
      this.runId = null;
      this.doc = null;
      this.error = null;
      this.loading = true;
      this.openKey = null;
      this.step = null;
      this.log = null;
      this.watchRoot = null;
      this.refresh(this.acx);
    } else if (this.doc == null && !this.inflight && !this.error) {
      this.refresh(this.acx);
    }
  }

  hide() {
    this.visible = false;
  }

  view(viewSwitch) {
    const ctx = this.ctxOf();
    return renderWorkflows({
      on: ctx.on,
      runs: this.runs,
      runId: this.runId,
      doc: this.doc,
      error: this.error,
      loading: this.loading,
      openKey: this.openKey,
      step: this.step,
      log: this.log,
      filter: this.filter,
      collapsed: this.collapsed,
      viewSwitch,
      nowMs: Date.now(),
      onPickRun: (id) => this.pickRun(id),
      onOpenItem: (key) => this.openItem(key),
      onBack: () => this.back(),
      onStep: (step) => this.selectStep(step),
      onFilter: (id) => this.setFilter(id),
      onToggleLane: (id) => this.toggleLane(id),
      onRefresh: () => this.refresh(this.acx),
    });
  }

  attention() {
    return attentionCount(this.doc);
  }

  refresh(cx) {
    if (this.inflight) {
      this.again = true;
      return;
    }
    this.inflight = true;
    cx.spawn(async (cx) => {
      try { await this.pull(cx); }
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

  fail(err) {
    this.error = err;
    this.loading = false;
  }

  async pull(cx) {
    const ctx = this.ctxOf();
    const key = ctxKey(ctx);
    const listed = await invoke(exec, ctx, "pr", listArgv());
    // The focused space moved to another machine mid-pull: its answer belongs to no one now.
    if (ctxKey(this.ctxOf()) !== key) return;
    if (!listed.ok) return this.fail(listed.error);
    const listJson = readJson(listed.stdout);
    if (!listJson.ok) return this.fail(listJson.error);
    const parsed = parseRunList(listJson.value);
    if (!parsed.ok) return this.fail(parsed.error);
    this.runs = parsed.runs;
    if (this.runId && !this.runs.some((r) => r.id === this.runId)) this.runId = null;
    const id = this.runId ?? this.runs[0]?.id;
    if (!id) {
      this.doc = null;
      this.error = null;
      this.loading = false;
      this.watchRoot = null;
      return;
    }
    const ran = await invoke(exec, ctx, "pr", statusArgv(id));
    if (ctxKey(this.ctxOf()) !== key) return;
    if (!ran.ok) return this.fail(ran.error);
    const body = readJson(ran.stdout);
    if (!body.ok) return this.fail(body.error);
    const doc = parseRunDoc(body.value);
    if (!doc.ok) return this.fail(doc.error);
    // A run picked mid-pull supersedes this answer; the re-run via `again` fetches the new one.
    if (this.runId !== null && this.runId !== id) return;
    this.doc = doc;
    this.error = null;
    this.loading = false;
    if (ctx.on != null) {
      this.watchRoot = null;
      return;
    }
    const runDir = doc.run.runDir;
    if (!runDir) {
      this.watchRoot = null;
      return;
    }
    this.startWatch(cx, runDir);
  }

  startWatch(cx, runDir) {
    if (!runDir || this.watchRoot === runDir) return;
    this.watchRoot = runDir;
    cx.spawn(async (cx) => {
      try {
        while (this.watchRoot === runDir && this.ctxOf().on == null) {
          const paths = await watchFolder(runDir);
          if (this.watchRoot !== runDir || this.ctxOf().on != null) break;
          if (!Array.isArray(paths) || stateChanged(paths, runDir)) this.refresh(cx);
        }
      } catch {
        // watchFolder rejected; local updates wait until runDir changes and the loop restarts.
      }
    });
  }

  stepStatus() {
    if (!this.openKey || !this.step) return null;
    const item = this.doc?.items?.find((i) => i.key === this.openKey);
    return item?.steps?.[this.step]?.status ?? null;
  }

  pickRun(id) {
    if (this.runId === id) return;
    this.runId = id;
    this.openKey = null;
    this.step = null;
    this.log = null;
    this.refresh(this.acx);
    this.acx.notify();
  }

  openItem(key) {
    this.openKey = key;
    const item = this.doc?.items?.find((i) => i.key === key);
    this.step = defaultStep(item);
    this.log = null;
    if (this.step) this.loadLog(this.acx);
    this.acx.notify();
  }

  back() {
    this.openKey = null;
    this.step = null;
    this.log = null;
    this.acx.notify();
  }

  selectStep(step) {
    this.step = step;
    this.log = null;
    this.loadLog(this.acx);
    this.acx.notify();
  }

  setFilter(id) {
    this.filter = this.filter === id ? null : id;
    this.acx.notify();
  }

  toggleLane(id) {
    if (this.collapsed.has(id)) this.collapsed.delete(id);
    else this.collapsed.add(id);
    this.acx.notify();
  }

  loadLog(cx) {
    const key = this.openKey;
    const step = this.step;
    if (!key || !step) return;
    const item = this.doc?.items?.find((i) => i.key === key);
    const path = item?.steps?.[step]?.logPath;
    if (!path) {
      this.log = { error: "No log for this step" };
      cx.notify();
      return;
    }
    if (this.logInflight === path) return;
    const argv = logTailArgv(path);
    const ctx = this.ctxOf();
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
