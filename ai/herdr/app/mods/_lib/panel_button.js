/**
 * Tab-bar toggle that selects a panel-body child. herdr's tabs mod hard-codes
 * Files/Changes, so each extra panel is a sibling block in tabs-row.
 * Cannot be loaded by node tests because it imports herdr.
 */
import { View, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { run, theme, shared, setShared, layout } from "herdr";
import { PANEL_BUTTON, RADIUS, SP } from "./tokens.js";
import { type } from "../_ui/ui.js";
import { fullscreenState, healDue } from "./fullscreen.js";
import { exitFullscreen } from "./fullscreen_run.js";
import { panelOn, enableWorkspacePanel, clickSteps } from "./panel_toggle.js";

export function panelButton({ child, label, icon, buttonId }) {
  return class PanelButton extends View {
    init(_props, cx) {
      // herdr's tabs mod remembers each space's panel only among `workspace.panels`;
      // without this child listed there, switching spaces closes it.
      const next = enableWorkspacePanel(shared("workspace.panels"), child);
      if (next) setShared("workspace.panels", next);
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
        const on = panelOn(root, child);
        if (on !== this.on) {
          this.on = on;
          cx.notify();
        }
      });
    }

    render(_cx) {
      const t = theme();
      const on = panelOn(layout(), child);
      return h_flex().size_full().items_center().pr(SP.sm).bg(t.chrome).border_b_1().border_color(t.border)
        .child(type(h_flex().id(buttonId).h(PANEL_BUTTON.h).px(PANEL_BUTTON.px).gap(PANEL_BUTTON.gap).flex_none().items_center()
          .rounded(RADIUS.control).cursor_pointer()
          // DESIGN.md: selected is never fill alone, so the open panel also takes a 2px accent rule
          // under the label; hover fills only the closed toggle, so it never hides the selection.
          .when(on, (e) => e.bg(t.selected).text_color(t.foreground).border_b_2().border_color(t.accent))
          .when(!on, (e) => e.bg(t.chrome).text_color(t.muted).hover((s) => s.bg(t.hover)))
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
          .on_click(() => {
            if (fullscreenState(layout()) !== "off") exitFullscreen();
            for (const step of clickSteps(on, child)) run(step.command, step.args);
          })
          .child(svg(icon).size(PANEL_BUTTON.icon))
          .child(label), "label"));
    }
  };
}
