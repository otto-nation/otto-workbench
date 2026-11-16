// Space PRs: one row per open space whose checkout is on an open PR. Listing-only pulls;
// the row selects the space, the #N ref opens that PR's target session.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, spaces, panes, run, theme } from "herdr";
import { createBoardStore, ctxKey, FULL_PULL_MS } from "./_lib/board_store.js";
import { spaceTargets } from "./_lib/scope.js";
import { spacePrs } from "./_lib/space_prs.js";
import { openSession } from "./_lib/run_session.js";
import { createLoadGate, execLoadReader } from "./_lib/load_backoff.js";
import { BRAND } from "./_lib/brand.generated.js";
import { SP, ROW_H } from "./_lib/tokens.js";
import { pane, scrollBody, hoverRow, text, type, tag, badge, placeholder, statusText } from "./_ui/ui.js";

const LISTING_MS = 15000;

function uiKey(row) {
  return `${row.on ?? ""}\0${row.cwd ?? ""}`;
}

export default class SpacePrs extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.store = createBoardStore({ exec });
    this.inflight = this.again = false;
    this.key = null;
    this.size = null;
    this.sessionUi = new Map();
    // The same load backoff as the board's timers: a busy machine stretches the poll.
    const gate = createLoadGate({ read: execLoadReader(exec) });
    cx.timer.every(LISTING_MS, (cx) => { if (gate.pass()) this.refresh(cx); });
    this.refresh(cx);
  }

  // Overlapping listing pulls coalesce: one in flight, at most one queued behind it.
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

  targets() {
    return spaceTargets(spaces(), panes());
  }

  async pull(cx) {
    const all = this.targets();
    await this.store.pull({
      targets: this.store.active(all),
      listingOnly: true,
      refreshAfterMs: 2 * FULL_PULL_MS,
      keep: () => this.targets(),
      isCurrent: (key) => this.store.active(this.targets()).some((c) => ctxKey(c) === key),
    }, {
      onChange: () => cx.notify(),
    });
  }

  openTarget(row) {
    const key = uiKey(row);
    if (this.sessionUi.get(key)?.opening) return;
    this.sessionUi.set(key, { opening: true, error: null });
    const st = this.store.repoStateOf(row);
    this.acx.spawn(async (cx) => {
      let res;
      try {
        res = await openSession({ exec, run, panes, spaces }, "target", {
          on: row.on,
          cwd: row.cwd,
          branch: row.branch,
          repo: st.repo?.slug ?? null,
          features: st.features ?? null,
        });
      } catch (err) {
        // openSession never rejects; a throwing host call still ends "Opening".
        res = { ok: false, message: String(err?.message ?? err) };
      }
      this.sessionUi.set(key, { opening: false, error: res.ok ? null : res.message });
      cx.notify();
    });
    this.acx.notify();
  }

  selectSpace(row) {
    run("space.select", { id: row.spaceId });
  }

  // Nested click: stop the press so the row's space.select does not also fire.
  refControl(row, ui) {
    const t = theme();
    const ref = type(h_flex().id(`space-prs-ref-${row.spaceId}`).flex_none().items_center(), "code")
      .cursor_pointer()
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
      .on_click((_e, cx) => {
        if (typeof cx?.stop_propagation === "function") cx.stop_propagation();
        this.openTarget(row);
      })
      .child(`#${row.pr.number}`);
    const extra = ui.opening
      ? statusText("running", null, { aria: "Opening" })
      : (ui.error ? text("meta", ui.error, t.muted) : null);
    if (!extra) return ref;
    return h_flex().flex_none().gap(SP.xs).items_center().child(ref).child(extra);
  }

  rowBody(row, rail) {
    const t = theme();
    const ui = this.sessionUi.get(uiKey(row)) ?? {};
    if (row.status === "loading") {
      return statusText("running", null, { aria: "Loading" });
    }
    if (row.status === "error") {
      const mark = statusText("fail", null, { aria: row.message ?? "Failed" });
      if (rail) return mark;
      return h_flex().gap(SP.sm).items_center().min_w(0)
        .child(mark)
        .when(Boolean(row.message), (e) => e.child(text("meta", row.message, t.muted).min_w(0).truncate()));
    }
    const p = row.pr?.presentation;
    if (rail) return row.pr ? this.refControl(row, ui) : null;
    return h_flex().gap(SP.sm).items_center().flex_wrap().min_w(0)
      .child(type(div().min_w(0).flex_1().truncate(), "body").child(row.spaceName))
      .child(text("code", row.branch, t.muted).flex_none())
      .when(Boolean(row.pr), (e) => e.child(this.refControl(row, ui)))
      .children((p?.tags ?? []).map(tag))
      .children((p?.row_badges ?? []).map(badge))
      .when(row.more > 0, (e) => e.child(text("meta", `+${row.more}`, t.muted).flex_none()));
  }

  renderRow(row, rail) {
    return hoverRow(`space-prs-${row.spaceId}`)
      .min_h(rail ? ROW_H.listDense : ROW_H.sidebar)
      .flex_none().px(rail ? SP.xs : SP.md).justify_center()
      .on_click(() => this.selectSpace(row))
      .child(this.rowBody(row, rail));
  }

  onSize(size, cx) {
    this.size = size;
    cx.notify();
  }

  render(_cx) {
    const all = this.targets();
    const scopeKey = JSON.stringify(all.map(ctxKey));
    if (this.key !== scopeKey) {
      this.key = scopeKey;
      this.refresh(this.acx);
    }
    const rows = spacePrs({ spaces: spaces(), panes: panes(), stateOf: this.store.repoStateOf });
    const rail = (this.size?.width ?? Infinity) < BRAND.pane.compact;
    const body = !rows.length
      ? placeholder("No open PRs in these spaces")
      : scrollBody().children(rows.map((row) => this.renderRow(row, rail)));
    return pane("space-prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(v_flex().size_full().min_h(0).p(rows.length ? 0 : SP.md).child(body));
  }
}
