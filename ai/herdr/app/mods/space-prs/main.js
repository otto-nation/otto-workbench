// Space PRs: one group per repo among the open spaces, rows for OPEN PRs that
// have a worktree. Listing-only pulls; the group header selects the space, the
// card opens an owner or shell in that worktree, else opens a target session.
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
  pane, scrollBody, card, hoverRow, text, type, tag, badge, placeholder, statusText, sectionLabel,
  paneHeader, pinned,
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

  // #N is code-role text; Opening / error sit beside it. The row click opens the target.
  refLabel(row, ui) {
    const t = theme();
    const ref = type(h_flex().flex_none().items_center(), "code").child(`#${row.pr.number}`);
    const extra = ui.opening
      ? statusText("running", null, { aria: "Opening" })
      : (ui.error ? text("meta", ui.error, t.muted) : null);
    if (!extra) return ref;
    return h_flex().flex_none().gap(SP.xs).items_center().child(ref).child(extra);
  }

  // Wide: the branch with its #N on the right, then a meta line of problem text, `+N` and the
  // owning agent. Rail: only the #N.
  rowBody(group, row, rail) {
    const t = theme();
    const ui = this.sessionUi.get(uiKey(row)) ?? {};
    if (rail) return row.pr ? this.refLabel(row, ui) : null;
    const p = row.pr?.presentation;
    const details = [
      ...(p?.tags ?? []).map(tag),
      ...(p?.row_badges ?? []).map(badge),
      ...(row.more > 0 ? [text("meta", `+${row.more}`, t.muted).flex_none()] : []),
      ...(row.owner?.name ? [text("meta", row.owner.name, t.muted).min_w(0).truncate()] : []),
    ];
    return v_flex().min_w(0)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(text("code", row.branch, t.muted).min_w(0).flex_1().truncate())
        .when(Boolean(row.pr), (e) => e.child(this.refLabel(row, ui))))
      .when(details.length > 0, (e) => e.child(
        h_flex().gap(SP.sm).items_center().flex_wrap().min_w(0).children(details)));
  }

  renderRow(group, row, rail) {
    const view = clickRow(group, row);
    const id = `space-prs-${group.spaceId}-${view.pr?.number ?? view.branch}`;
    const onClick = () => view.pr ? this.openTarget(view) : this.selectSpace(group);
    if (rail) {
      if (!view.pr) return null;
      return hoverRow(id)
        .flex().flex_none().min_h(ROW_H.listDense).px(SP.xs).items_center()
        .on_click(onClick)
        .child(this.rowBody(group, view, rail));
    }
    return card(id)
      .flex_none()
      .mx(SP.xs)
      .on_click(onClick)
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
      .flex_none().px(rail ? SP.xs : SP.md).mx(rail ? 0 : SP.xs)
      .when(rail, (e) => e.min_h(ROW_H.listDense).justify_center())
      .when(!rail, (e) => e.pt(SP.md).pb(SP.xs))
      .on_click(() => this.selectSpace(group))
      .child(sectionLabel(group.spaceName));
    const status = this.groupStatus(group, rail);
    return v_flex().flex_none()
      .child(header)
      .when(status != null, (e) => e.child(
        h_flex().flex_none().px(rail ? SP.xs : SP.md).mx(rail ? 0 : SP.xs).min_h(ROW_H.listDense).items_center().child(status),
      ))
      .child(v_flex().flex_none().gap(rail ? 0 : SP.xs).pb(SP.xs)
        .children((group.rows ?? []).map((row) => this.renderRow(group, row, rail)).filter((el) => el != null)));
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
    const t = theme();
    const header = rail
      ? pinned(v_flex().px(SP.xs).py(SP.xxs).child(text("section", "PRs", t.muted)))
      : paneHeader("Space PRs", {
          spinning: this.inflight,
          onRefresh: () => this.refresh(this.acx),
          id: "space-prs-refresh",
        });
    const body = !groups.length
      ? placeholder("No PR worktrees in these spaces").px(SP.md).mx(SP.xs).py(SP.sm)
      : scrollBody().children(groups.map((group) => this.renderGroup(group, rail)));
    // herdr draws its sidebars on `chrome`, not the terminals' background.
    return pane("space-prs")
      .bg(theme().chrome)
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(v_flex().size_full().min_h(0).child(header).child(body));
  }
}
