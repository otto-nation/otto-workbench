// Space PRs: one group per repo among the open spaces, rows for OPEN PRs that
// have a worktree. Listing-only pulls; the group header selects the space, the
// #N ref focuses an owner or shell in that worktree, else opens a target session.
import { View } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, spaces, panes, run, theme, terminals, agents } from "herdr";
import { createBoardStore, ctxKey, FULL_PULL_MS } from "./_lib/board_store.js";
import { spaceTargets } from "./_lib/scope.js";
import { spaceWorktreePrs } from "./_lib/space_prs.js";
import { openSession, agentPaneFor, focusSteps } from "./_lib/run_session.js";
import { createLoadGate, execLoadReader } from "./_lib/load_backoff.js";
import { BRAND } from "./_lib/brand.generated.js";
import { SP, ROW_H } from "./_lib/tokens.js";
import {
  pane, scrollBody, hoverRow, text, type, tag, badge, placeholder, statusText, sectionLabel,
} from "./_ui/ui.js";

const LISTING_MS = 15000;

function uiKey(row) {
  return `${row.on ?? ""}\0${row.worktree ?? ""}`;
}

function clickRow(group, row) {
  return { ...row, on: group.on, spaceId: group.spaceId, repo: group.repo };
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

  // The group's own repo (docToState keeps the checkout it was read from), not the machine's first.
  repoState(row) {
    return this.store.repoStateOf({ on: row.on ?? null, cwd: row.repo?.cwd ?? null });
  }

  focusAddress(address) {
    for (const [name, args] of focusSteps(address, panes(), spaces())) run(name, args);
  }

  openTarget(row) {
    const key = uiKey(row);
    if (this.sessionUi.get(key)?.opening) return;
    // The owning agent (by label, then cwd) ranks first; a shell in the worktree is the fallback.
    const pane = agentPaneFor(panes(), {
      on: row.on, cwd: row.worktree, terminals: terminals(), shells: true,
    });
    if (pane) {
      this.focusAddress(pane.terminal);
      this.sessionUi.set(key, { opening: false, error: null });
      this.acx.notify();
      return;
    }
    this.sessionUi.set(key, { opening: true, error: null });
    const st = this.repoState(row);
    this.acx.spawn(async (cx) => {
      let res;
      try {
        res = await openSession({ exec, run, panes, spaces, terminals }, "target", {
          on: row.on,
          cwd: row.worktree,
          branch: row.branch,
          repo: st.repo?.slug ?? row.repo?.slug ?? null,
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

  // Nested click: stop the press so a parent handler does not also fire.
  refControl(group, row, ui) {
    const t = theme();
    const ref = type(h_flex().id(`space-prs-ref-${group.spaceId}-${row.pr.number}`).flex_none().items_center(), "code")
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

  rowBody(group, row, rail) {
    const t = theme();
    const ui = this.sessionUi.get(uiKey(row)) ?? {};
    if (rail) return row.pr ? this.refControl(group, row, ui) : null;
    const p = row.pr?.presentation;
    return h_flex().gap(SP.sm).items_center().flex_wrap().min_w(0)
      .child(text("code", row.branch, t.muted).min_w(0).truncate())
      .when(Boolean(row.pr), (e) => e.child(this.refControl(group, row, ui)))
      .children((p?.tags ?? []).map(tag))
      .children((p?.row_badges ?? []).map(badge))
      .when(row.more > 0, (e) => e.child(text("meta", `+${row.more}`, t.muted).flex_none()))
      .when(Boolean(row.owner?.name), (e) => e.child(text("meta", row.owner.name, t.muted).min_w(0).truncate()));
  }

  renderRow(group, row, rail) {
    const view = clickRow(group, row);
    return h_flex()
      .id(`space-prs-${group.spaceId}-${view.pr?.number ?? view.branch}`)
      .min_h(rail ? ROW_H.listDense : ROW_H.sidebar)
      .flex_none().px(rail ? SP.xs : SP.md).items_center()
      .child(this.rowBody(group, view, rail));
  }

  groupStatus(group, rail) {
    const t = theme();
    if (group.status === "loading") {
      return statusText("running", null, { aria: "Loading" });
    }
    if (group.status === "error") {
      const mark = statusText("fail", null, { aria: group.message ?? "Failed" });
      if (rail) return mark;
      return h_flex().gap(SP.sm).items_center().min_w(0)
        .child(mark)
        .when(Boolean(group.message), (e) => e.child(text("meta", group.message, t.muted).min_w(0).truncate()));
    }
    if (group.status === "outdated") {
      return text("meta", group.message, t.muted).min_w(0).truncate();
    }
    return null;
  }

  renderGroup(group, rail) {
    const header = hoverRow(`space-prs-${group.spaceId}`)
      .min_h(rail ? ROW_H.listDense : ROW_H.sidebar)
      .flex_none().px(rail ? SP.xs : SP.md).justify_center()
      .on_click(() => this.selectSpace(group))
      .child(sectionLabel(group.spaceName));
    const status = this.groupStatus(group, rail);
    return v_flex().flex_none()
      .child(header)
      .when(status != null, (e) => e.child(
        h_flex().flex_none().px(rail ? SP.xs : SP.md).min_h(ROW_H.listDense).items_center().child(status),
      ))
      .children((group.rows ?? []).map((row) => this.renderRow(group, row, rail)));
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
    const groups = spaceWorktreePrs({
      spaces: spaces(),
      panes: panes(),
      terminals: terminals(),
      agents: agents(),
      stateOf: this.store.repoStateOf,
    });
    const rail = (this.size?.width ?? Infinity) < BRAND.pane.compact;
    const body = !groups.length
      ? placeholder("No PR worktrees in these spaces").px(SP.md).py(SP.sm)
      : scrollBody().children(groups.map((group) => this.renderGroup(group, rail)));
    return pane("space-prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(v_flex().size_full().min_h(0).child(body));
  }
}
