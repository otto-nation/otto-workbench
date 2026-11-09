// team-prs entry: the board, with a PR's detail shown in the same pane when a card is opened.
import { exec, readTextFile, run, notify, spaces } from "herdr";
import Board from "./board_view.js";
import { renderDetail, renderActionBar, renderOutput, detailOf } from "./detail.js";
import { renderFileList, renderDiff } from "./files_view.js";
import { MONO } from "./_lib/tokens.js";
import { actionsFor } from "./_lib/actions.js";
import { readReview, loadFiles } from "./_lib/board_data.js";
import { patchRows, summarize } from "./_lib/diff.js";
import { switchArgv, parseSwitch, openForReviewState, spaceFor } from "./_lib/worktree.js";
import { hcRunArgv, parseRunReply, outcomeText } from "./_lib/launch.js";
import { getArgv, terminalsOf } from "./_lib/busy.js";

export default class TeamPrs extends Board {
  init(p, cx) {
    this.detail = null;
    this.openStamp = null;
    this.confirm = null;
    this.launching = false;
    this.live = null;
    this.finishedId = null;
    this.note = null;
    this.output = null;
    // Terminals being followed to their end, whichever PR is open, and the action each ran.
    this.watching = new Set();
    this.actionOf = new Map();
    this.reviews = new Map();
    this.tab = "history";
    this.filesByPr = new Map();
    this.openFile = null;
    this.diff = null;
    this.opening = false;
    super.init(p, cx);
  }

  runOf(stamp) {
    const run = (this.detail.runs ?? []).find((r) => (r.stamp ?? "") === stamp);
    return run ? { reviewFile: run.review_file ?? "" } : undefined;
  }

  cacheKey(run) {
    return `${this.detail.on ?? ""}\0${run.reviewFile}`;
  }

  bodyFor(stamp) {
    const run = this.runOf(stamp);
    if (!run) return null;
    const hit = this.reviews.get(this.cacheKey(run));
    if (!hit || hit.stamp !== stamp) return null;
    return hit.result;
  }

  onToggle(stamp) {
    this.openStamp = this.openStamp === stamp ? null : stamp;
    if (this.openStamp) this.loadReview(this.openStamp);
    this.acx.notify();
  }

  loadReview(stamp) {
    const run = this.runOf(stamp);
    if (!run) return;
    const key = this.cacheKey(run);
    const hit = this.reviews.get(key);
    if (hit && hit.stamp === stamp && hit.result !== undefined && hit.result.ok !== false) return;
    if (hit && hit.stamp !== stamp) this.reviews.delete(key);
    this.reviews.set(key, { stamp, result: undefined });
    this.acx.spawn(async (cx) => {
      const result = await readReview(readTextFile, run.reviewFile, this.detail.on);
      const cur = this.reviews.get(key);
      if (!cur || cur.stamp !== stamp) return;
      this.reviews.set(key, { stamp, result });
      cx.notify();
    });
  }

  actions() {
    const d = this.detail;
    return actionsFor({
      repo: d.repo, number: d.number, repoDir: d.cwd, run: (d.runs ?? [])[0] ?? null,
      headMoved: d.headMoved, batchBusy: d.batchBusy || this.launching, live: this.launching ? null : (this.live ?? d.live),
      finished: this.finishedId,
    });
  }

  // Post actions ask first; everything else runs at once.
  onAction(action, cx) {
    if (action.confirm) this.confirm = action;
    else this.launch(action, cx);
    this.acx.notify();
  }

  // Kill and watch are core commands; the rest start a labelled background terminal through hc
  // (its --bg reply is immediate, so the 30 s exec limit never applies to the pr run itself).
  launch(action, cx) {
    if (action.show) {
      // Watch toggles the in-pane output; a second click hides it.
      this.output = this.output?.terminal === action.show ? null : { terminal: action.show, text: null };
      if (this.output) this.readOutput(action.show);
      this.acx.notify();
      return;
    }
    if (action.command) {
      run(action.command.name, action.command.args);
      return;
    }
    const d = this.detail;
    this.launching = true;
    this.note = null;
    this.acx.spawn(async (cx) => {
      let res;
      try {
        const argv = hcRunArgv({
          repo: d.repo, number: d.number, action: action.id, cwd: d.cwd, on: d.on, argv: action.argv,
        });
        res = parseRunReply(await exec("hc", argv, {}));
      } catch (err) {
        res = { ok: false, error: { message: String(err?.message ?? err) } };
      }
      this.launching = false;
      if (!res.ok) {
        this.note = res.error.message;
      } else {
        // The run is followed to its end even if another PR was opened meanwhile; only the
        // open PR's own controls track it.
        this.actionOf.set(res.terminal, action.id);
        if (this.detail?.repo === d.repo && this.detail?.number === d.number) {
          this.live = res.terminal;
          this.finishedId = null;
        }
        if (!this.watching.has(res.terminal)) {
          this.watching.add(res.terminal);
          this.watch(cx, res.terminal, d);
        }
      }
      cx.notify();
    });
    this.acx.notify();
  }

  readOutput(id) {
    this.acx.spawn(async () => {
      let text = null;
      try {
        const ran = await exec("hc", ["read", id, "--lines", "80"], {});
        text = ran.code === 0 ? ran.stdout : "";
      } catch { text = ""; }
      if (this.output?.terminal !== id) return;
      this.output = { terminal: id, text };
      this.acx.notify();
    });
  }

  // Until the terminal leaves "live", look again every 3 s: bounded by the run, and only while
  // this window is open. The chain is tied to the terminal it was started for.
  watch(cx, id, pr) {
    cx.timer.after(3000, async (cx) => {
      if (!this.watching.has(id)) return;
      let entry = null;
      try {
        const ran = await exec("hc", getArgv(id), {});
        entry = terminalsOf(ran.code === 0 ? [JSON.parse(ran.stdout)] : null)[0] ?? null;
      } catch { entry = null; }
      if (this.output?.terminal === id) this.readOutput(id);
      if (entry?.state === "live") { this.watch(cx, id, pr); return; }
      this.finish(id, entry, pr, cx);
    });
  }

  finish(id, entry, d, cx) {
    const outcome = outcomeText(this.actionOf.get(id), entry?.exitCode ?? null);
    this.watching.delete(id);
    if (this.live === id) {
      this.finishedId = id;
      this.live = null;
    }
    if (this.output?.terminal === id) this.readOutput(id);
    notify({ title: outcome.title, body: `#${d.number} ${d.title ?? ""}`, level: outcome.level });
    // The card, its runs and the head-moved flag come from the board's own refresh.
    this.refresh(cx, { listingOnly: true });
    cx.notify();
  }

  filesKey(d) {
    return `${d.on ?? ""}\0${d.repo}#${d.number}@${d.sha}`;
  }

  // Loaded once per PR head; a new head is a new key.
  ensureFiles(d) {
    const key = this.filesKey(d);
    if (this.filesByPr.has(key)) return this.filesByPr.get(key);
    if (this.filesByPr.size >= 10) this.filesByPr.delete(this.filesByPr.keys().next().value);
    this.filesByPr.set(key, null);
    this.acx.spawn(async (cx) => {
      let result;
      try {
        result = await loadFiles(exec, { cwd: d.cwd, on: d.on }, d.repo, d.number);
      } catch (err) {
        result = { ok: false, error: { message: String(err?.message ?? err) } };
      }
      this.filesByPr.set(key, result);
      cx.notify();
    });
    return null;
  }

  pickFile(path) {
    this.openFile = path;
    const f = this.filesByPr.get(this.filesKey(this.detail))?.files?.find((x) => x.path === path);
    this.diff = f ? patchRows(f.patch, MONO.charPx) : null;
    this.acx.notify();
  }

  // Check the PR out with the workbench's wt, then land in a space on that worktree.
  openForReview() {
    const d = this.detail;
    if (this.opening || !openForReviewState(d).enabled) return;
    this.opening = true;
    this.note = "Checking out…";
    this.acx.spawn(async (cx) => {
      let note = null;
      try {
        const ran = await exec("wt", switchArgv(d.number), { cwd: d.cwd });
        const got = ran.code === 0
          ? parseSwitch(ran.stdout ?? "")
          : { ok: false, message: (ran.stderr ?? "").trim().split("\n").pop() || "wt failed" };
        if (!got.ok) {
          note = got.message;
        } else {
          const id = spaceFor(spaces(), got.path);
          if (id) run("space.select", { id });
          else run("space.new", { folder: got.path, name: `#${d.number}`, argv: ["pi"] });
        }
      } catch (err) {
        note = String(err?.message ?? err);
      }
      if (this.detail?.repo !== d.repo || this.detail?.number !== d.number) return cx.notify();
      this.opening = false;
      this.note = note;
      cx.notify();
    });
    this.acx.notify();
  }

  // Switching PRs starts a clean slate for the transient action state.
  detailView(pr, cx) {
    const prev = this.detail;
    this.detail = detailOf(pr);
    if (prev && (prev.repo !== this.detail.repo || prev.number !== this.detail.number)) {
      this.openStamp = null; this.confirm = null; this.note = null; this.live = null; this.finishedId = null;
      this.output = null; this.tab = "history"; this.openFile = null; this.diff = null; this.opening = false;
    }
    const files = this.ensureFiles(this.detail);
    // The header counts what people wrote: once the files are known, generated ones drop out.
    if (files?.ok) {
      const sum = summarize(files.files);
      Object.assign(this.detail, { files: sum.files, additions: sum.add, deletions: sum.del, generated: sum.generated });
    }
    const file = this.openFile && files?.files?.find((f) => f.path === this.openFile);
    return renderDetail(this.detail, {
      nowMs: Date.now(),
      openStamp: this.openStamp,
      bodyFor: (stamp) => this.bodyFor(stamp),
      onToggle: (stamp) => this.onToggle(stamp),
      actionBar: renderActionBar(this.actions(), {
        confirm: this.confirm,
        note: this.launching ? "Starting…" : this.note,
        onAction: (a) => this.onAction(a, cx),
        onConfirm: () => { const a = this.confirm; this.confirm = null; this.launch(a, cx); this.acx.notify(); },
        onCancel: () => { this.confirm = null; this.acx.notify(); },
      }),
      outputPanel: this.output && renderOutput({ ...this.output, live: this.live === this.output.terminal }, {
        onHide: () => { this.output = null; this.acx.notify(); },
        onTab: () => run("terminal.keep", { terminal: this.output.terminal }),
      }),
      tab: this.tab,
      onTab: (tab) => { this.tab = tab; this.openFile = null; this.diff = null; this.acx.notify(); },
      filesBody: file && this.diff
        ? renderDiff(file, this.diff, { onBack: () => { this.openFile = null; this.diff = null; this.acx.notify(); } })
        : renderFileList(files, { onPick: (path) => this.pickFile(path) }),
      reviewState: openForReviewState(this.detail),
      onReview: () => this.openForReview(),
      onClose: () => this.closeDetail(cx),
      // The browser is on this Mac even when the PR's repo is on a remote machine.
      onOpenUrl: (url) => exec("open", [url]),
    });
  }
}
