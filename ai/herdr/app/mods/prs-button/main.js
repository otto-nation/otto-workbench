/**
 * The PRs toggle beside herdr's tab bar. herdr's tabs mod hard-codes its Files and Changes
 * buttons, so this block sits next to it (the workbench layout puts both in one row) and drives
 * the same right-hand panel: the `panel` column showing the `prs-panel` child of `panel-body`.
 */
import { View, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { run, theme, shared, setShared, layout } from "herdr";
import { PANEL_BUTTON, RADIUS, SP } from "./_lib/tokens.js";
import { type } from "./_ui/ui.js";
import { findNode, fullscreenState, healDue } from "./_lib/fullscreen.js";
import { exitFullscreen } from "./_lib/fullscreen_run.js";

const CHILD = "prs-panel";

function panelOn(root) {
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  return Boolean(panel && !panel.hidden && body?.active === CHILD);
}

export default class PrsButton extends View {
  init(_props, cx) {
    // herdr's tabs mod remembers each space's panel only among `workspace.panels`; without the
    // board listed there, switching spaces closes it.
    const enabled = shared("workspace.panels") ?? ["files", "changes"];
    if (!enabled.includes(CHILD)) setShared("workspace.panels", [...enabled, CHILD]);
    this.healUntil = 0;
    this.on = null;
    // The app must never start with the terminals hidden.
    if (fullscreenState(layout()) !== "off") exitFullscreen();
    cx.timer.every(1000, (cx) => {
      const root = layout();
      if (healDue(fullscreenState(root), this.healUntil, Date.now())) {
        this.healUntil = Date.now() + 1500;
        exitFullscreen();
      }
      const on = panelOn(root);
      if (on !== this.on) {
        this.on = on;
        cx.notify();
      }
    });
  }

  render(_cx) {
    const t = theme();
    const on = panelOn(layout());
    return h_flex().size_full().items_center().pr(SP.sm).bg(t.chrome).border_b_1().border_color(t.border)
      .child(type(h_flex().id("panel-prs").h(PANEL_BUTTON.h).px(PANEL_BUTTON.px).gap(PANEL_BUTTON.gap).flex_none().items_center()
        .rounded(RADIUS.control).cursor_pointer()
        // DESIGN.md: selected is never fill alone, so the open board also takes a 2px accent rule
        // under the label; hover fills only the closed toggle, so it never hides the selection.
        .when(on, (e) => e.bg(t.selected).text_color(t.foreground).border_b_2().border_color(t.accent))
        .when(!on, (e) => e.bg(t.chrome).text_color(t.muted).hover((s) => s.bg(t.hover)))
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .on_click(() => {
          if (fullscreenState(layout()) !== "off") exitFullscreen();
          if (on) run("layout.hide", { id: "panel" });
          else { run("layout.select", { stack: "panel-body", child: CHILD }); run("layout.show", { id: "panel" }); }
        })
        .child(svg("mods/prs-button/pr.svg").size(PANEL_BUTTON.icon))
        .child("PRs"), "label"));
  }
}
