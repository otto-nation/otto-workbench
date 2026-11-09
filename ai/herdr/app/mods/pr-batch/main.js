// pr-batch: a read-only grid of `pr batch` runs on the focused space's machine.
import { View } from "gpui-kit";
import { exec, spaces, panes, focused, watchFolder } from "herdr";
import { locate } from "./_lib/location.js";
import {
  listArgv, statusArgv, logTailArgv, parseRunList, parseRunDoc, stateChanged,
} from "./_lib/batch.js";
import { invoke, readJson } from "./_lib/board_data.js";
import { renderPrBatch } from "./grid_view.js";

// Follows the focused space only; the team-prs pin is deliberately ignored, so this mod may show a
// different machine than the board.
function ctxOf() {
  return locate(spaces(), panes(), focused());
}

function ctxKey(ctx) {
  return String(ctx?.on ?? "");
}

export default class PrBatch extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.runs = [];
    this.runId = null;
    this.doc = null;
    this.error = null;
    this.loading = true;
    this.cell = null;
    this.log = null;
    this.inflight = this.again = false;
    // The log path being read, so a slow tail is not doubled by the 3 s timer; another step's
    // click still starts its own read.
    this.logInflight = null;
    this.watchRoot = null;
    this.key = ctxKey(ctxOf());
    cx.timer.every(15000, (cx) => {
      if (ctxOf().on != null) this.refresh(cx);
    });
    cx.timer.every(3000, (cx) => {
      if (this.cell && this.stepStatus() === "running") this.loadLog(cx);
    });
    this.refresh(cx);
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
    const ctx = ctxOf();
    const key = ctxKey(ctx);
    const listed = await invoke(exec, ctx, "pr", listArgv());
    // The focused space moved to another machine mid-pull: its answer belongs to no one now.
    if (ctxKey(ctxOf()) !== key) return;
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
    if (ctxKey(ctxOf()) !== key) return;
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
        while (this.watchRoot === runDir && ctxOf().on == null) {
          const paths = await watchFolder(runDir);
          if (this.watchRoot !== runDir || ctxOf().on != null) break;
          if (!Array.isArray(paths) || stateChanged(paths, runDir)) this.refresh(cx);
        }
      } catch {
        // watchFolder rejected; local updates wait until runDir changes and the loop restarts.
      }
    });
  }

  stepStatus() {
    const cell = this.cell;
    if (!cell) return null;
    const item = this.doc?.items?.find((i) => i.key === cell.key);
    return item?.steps?.[cell.step]?.status ?? null;
  }

  pickRun(id) {
    if (this.runId === id) return;
    this.runId = id;
    this.cell = null;
    this.log = null;
    this.refresh(this.acx);
    this.acx.notify();
  }

  toggleCell(key, step) {
    if (this.cell && this.cell.key === key && this.cell.step === step) {
      this.cell = null;
      this.log = null;
      this.acx.notify();
      return;
    }
    this.cell = { key, step };
    this.log = null;
    this.loadLog(this.acx);
    this.acx.notify();
  }

  closeLog() {
    this.cell = null;
    this.log = null;
    this.acx.notify();
  }

  loadLog(cx) {
    const cell = this.cell;
    if (!cell) return;
    const item = this.doc?.items?.find((i) => i.key === cell.key);
    const path = item?.steps?.[cell.step]?.logPath;
    if (!path) {
      this.log = { error: "No log for this step" };
      cx.notify();
      return;
    }
    if (this.logInflight === path) return;
    const argv = logTailArgv(path);
    const ctx = ctxOf();
    this.logInflight = path;
    cx.spawn(async (cx) => {
      let ran;
      try { ran = await invoke(exec, ctx, argv[0], argv.slice(1)); }
      finally { if (this.logInflight === path) this.logInflight = null; }
      if (!this.cell || this.cell.key !== cell.key || this.cell.step !== cell.step) return;
      this.log = ran.ok ? { text: ran.stdout ?? "" } : { error: ran.error.message };
      cx.notify();
    });
  }

  render(_cx) {
    const ctx = ctxOf();
    const key = ctxKey(ctx);
    if (this.key !== key) {
      this.key = key;
      this.runs = [];
      this.runId = null;
      this.doc = null;
      this.error = null;
      this.loading = true;
      this.cell = null;
      this.log = null;
      this.watchRoot = null;
      this.refresh(this.acx);
    }
    return renderPrBatch({
      on: ctx.on,
      runs: this.runs,
      runId: this.runId,
      doc: this.doc,
      error: this.error,
      loading: this.loading,
      cell: this.cell,
      log: this.log,
      onPickRun: (id) => this.pickRun(id),
      onCell: (key, step) => this.toggleCell(key, step),
      onCloseLog: () => this.closeLog(),
    });
  }
}
