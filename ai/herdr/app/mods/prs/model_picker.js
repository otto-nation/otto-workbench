// Models: a popover with one text field per phase, for trying a model on the next run.
// Nothing is saved; the values live for the session and ride on the run as
// `pr --phase-model` (see _lib/model_overrides.js). `pr` validates them.
import { div } from "gpui-kit";
import { h_flex, v_flex, Popover, Input, InputState } from "gpui-base";
import { theme } from "herdr";
import { overrideSummary } from "./_lib/model_overrides.js";
import { SP, RADIUS, CONTROL } from "./_lib/tokens.js";
import { ghostButton, popoverSurface, text, caption } from "./_ui/ui.js";

export class ModelPicker {
  constructor(acx, phases, id) {
    this.acx = acx;
    this.phases = phases;
    this.id = id;
    this.open = false;
    this.fields = new Map(phases.map((phase) => {
      const state = InputState.new({ placeholder: "tier default" });
      state.on("change", () => acx.notify());
      return [phase, state];
    }));
  }

  values() {
    return Object.fromEntries(this.phases.map((p) => [p, this.fields.get(p).value()]));
  }

  summary() {
    return overrideSummary(this.values(), this.phases);
  }

  clear() {
    for (const state of this.fields.values()) state.set_value("");
    this.acx.notify();
  }

  // The trigger plus, beside it, what is set: a button label is the action and nothing else.
  render() {
    const t = theme();
    const trigger = h_flex().flex_none().h(CONTROL.h).px(CONTROL.px).gap(SP.xs).items_center()
      .rounded(RADIUS.control).border_1().border_color(t.border)
      .hover((s) => s.bg(t.hover))
      .child(text("label", "Models"))
      .child(text("label", "▾", t.muted));
    const popover = Popover.new(`${this.id}-popover`)
      .open(this.open)
      .on_open_change((open) => { this.open = open; this.acx.notify(); })
      .trigger(trigger)
      .content(this.content());
    const summary = this.summary();
    return h_flex().flex_none().gap(SP.sm).items_center()
      .child(popover)
      .when(Boolean(summary), (e) => e.child(caption(summary)));
  }

  content() {
    const t = theme();
    return popoverSurface()
      .child(caption("Model for the next run, per phase. Blank uses the configured one. Review phases refuse light and Haiku models."))
      .children(this.phases.map((phase) => v_flex().gap(SP.xxs)
        .child(caption(phase))
        .child(Input.new(this.fields.get(phase)).h(CONTROL.h).px(SP.sm)
          .rounded(RADIUS.control).border_1().border_color(t.input).bg(t.background))))
      .child(div().flex_none().child(ghostButton(`${this.id}-clear`, "Clear", () => this.clear())));
  }
}
