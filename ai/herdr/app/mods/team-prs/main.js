// team-prs entry: the board, or a detail window when props().detail is set. A pane copy may have
// no props, so detail is read null-safely. The window copy never starts the board's timers.
import { exec, props, closeOverlay, readTextFile } from "herdr";
import Board from "./board_view.js";
import { renderDetail } from "./detail.js";
import { readReview } from "./_lib/board_data.js";
import { historyRows } from "./_lib/history.js";

export default class TeamPrs extends Board {
  init(p, cx) {
    this.detail = props()?.detail ?? null;
    this.openStamp = null;
    this.reviews = new Map();
    if (this.detail) return;
    super.init(p, cx);
  }

  runOf(stamp) {
    return historyRows(this.detail.runs ?? [], 0).find((r) => r.stamp === stamp);
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
    if (hit && hit.stamp === stamp && hit.result !== undefined) return;
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

  render(cx) {
    if (!this.detail) return super.render(cx);
    return renderDetail(this.detail, {
      nowMs: Date.now(),
      openStamp: this.openStamp,
      bodyFor: (stamp) => this.bodyFor(stamp),
      onToggle: (stamp) => this.onToggle(stamp, cx),
      onClose: () => closeOverlay(props().overlay),
      // The browser is on this Mac even when the PR's repo is on a remote machine.
      onOpenUrl: (url) => exec("open", [url]),
    });
  }
}
