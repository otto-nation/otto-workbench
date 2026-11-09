// team-prs entry: the board, with a PR's detail shown in the same pane when a card is opened.
import { exec, readTextFile, run, notify } from "herdr";
import Board from "./board_view.js";
import { renderDetail, renderActionBar, detailOf } from "./detail.js";
import { actionsFor } from "./_lib/actions.js";
import { readReview } from "./_lib/board_data.js";
import { hcRunArgv, parseRunReply, outcomeText } from "./_lib/launch.js";
import { getArgv, terminalsOf } from "./_lib/busy.js";

export default class TeamPrs extends Board {
  init(p, cx) {
    this.detail = null;
    this.openStamp = null;
    this.confirm = null;
    this.launching = false;
    this.live = null;
    this.liveAction = null;
    this.finishedId = null;
    this.note = null;
    this.reviews = new Map();
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

  onToggle(stamp, cx) {
    this.openStamp = this.openStamp === stamp ? null : stamp;
    if (this.openStamp) this.loadReview(this.openStamp, cx);
    cx.notify();
  }

  loadReview(stamp, cx) {
    const run = this.runOf(stamp);
    if (!run) return;
    const key = this.cacheKey(run);
    const hit = this.reviews.get(key);
    if (hit && hit.stamp === stamp && hit.result !== undefined && hit.result.ok !== false) return;
    if (hit && hit.stamp !== stamp) this.reviews.delete(key);
    this.reviews.set(key, { stamp, result: undefined });
    cx.spawn(async (cx) => {
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
    cx.notify();
  }

  // Kill and watch are core commands; the rest start a labelled background terminal through hc
  // (its --bg reply is immediate, so the 30 s exec limit never applies to the pr run itself).
  launch(action, cx) {
    if (action.command) {
      run(action.command.name, action.command.args);
      return;
    }
    const d = this.detail;
    this.launching = true;
    this.note = null;
    cx.spawn(async (cx) => {
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
      if (!res.ok) this.note = res.error.message;
      else {
        this.live = res.terminal;
        this.liveAction = action.id;
        this.finishedId = null;
        this.watch(cx);
      }
      cx.notify();
    });
    cx.notify();
  }

  // Until the terminal leaves "live", look again every 3 s: bounded by the run, and only while
  // this window is open.
  watch(cx) {
    cx.timer.after(3000, async (cx) => {
      const id = this.live;
      if (id == null) return;
      let entry = null;
      try {
        const ran = await exec("hc", getArgv(id), {});
        entry = terminalsOf(ran.code === 0 ? [JSON.parse(ran.stdout)] : null)[0] ?? null;
      } catch { entry = null; }
      if (entry?.state === "live") { this.watch(cx); return; }
      this.finish(id, entry, cx);
    });
  }

  finish(id, entry, cx) {
    const d = this.detail;
    const outcome = outcomeText(this.liveAction, entry?.exitCode ?? null);
    this.finishedId = id;
    this.live = null;
    notify({ title: outcome.title, body: `#${d.number} ${d.title ?? ""}`, level: outcome.level });
    // The card, its runs and the head-moved flag come from the board's own refresh.
    this.refresh(cx, { listingOnly: true });
    cx.notify();
  }

  // Switching PRs starts a clean slate for the transient action state.
  detailView(pr, cx) {
    const prev = this.detail;
    this.detail = detailOf(pr);
    if (prev && (prev.repo !== this.detail.repo || prev.number !== this.detail.number)) {
      this.openStamp = null; this.confirm = null; this.note = null; this.live = null; this.finishedId = null;
    }
    return renderDetail(this.detail, {
      nowMs: Date.now(),
      openStamp: this.openStamp,
      bodyFor: (stamp) => this.bodyFor(stamp),
      onToggle: (stamp) => this.onToggle(stamp, cx),
      actionBar: renderActionBar(this.actions(), {
        confirm: this.confirm,
        note: this.launching ? "Starting…" : this.note,
        onAction: (a) => this.onAction(a, cx),
        onConfirm: () => { const a = this.confirm; this.confirm = null; this.launch(a, cx); cx.notify(); },
        onCancel: () => { this.confirm = null; cx.notify(); },
      }),
      onClose: () => this.closeDetail(cx),
      // The browser is on this Mac even when the PR's repo is on a remote machine.
      onOpenUrl: (url) => exec("open", [url]),
    });
  }
}
