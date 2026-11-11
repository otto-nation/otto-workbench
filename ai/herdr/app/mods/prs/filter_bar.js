// Compact filter bar: Show / Repo / Label / Author popovers, tags, Clear.
// State transitions live in _lib/filters.js; this module only draws barModel().
import { div } from "gpui-kit";
import { h_flex, v_flex, Popover, Checkbox, Input, InputState } from "gpui-base";
import { theme } from "herdr";
import { searchOptions } from "./_lib/filters.js";
import { SP, RADIUS, CONTROL, LAYOUT, ROW_H } from "./_lib/tokens.js";
import { ghostButton, checkbox, popoverSurface, text, caption } from "./_ui/ui.js";

export class FilterBar {
  constructor(acx) {
    this.acx = acx;
    this.open = null;
    this.query = "";
    this.search = InputState.new({ placeholder: "Search authors" });
    this.search.on("change", () => {
      this.query = this.search.value();
      acx.notify();
    });
  }

  render(model, onAction, { trailing = null } = {}) {
    const t = theme();
    return h_flex().id("prs-filters").flex_none().flex_wrap().items_center()
      .gap(SP.sm).px(SP.lg).py(SP.sm).bg(t.background)
      .children((model.facets ?? []).map((f) => this.facet(f, onAction)))
      .child(this.tagsRow(model.tags ?? [], onAction))
      .child(div().flex_1())
      .when(trailing != null, (e) => e.child(trailing))
      .when(Boolean(model.clear), (e) => e.child(
        ghostButton("filters-clear", "Clear filters", () => onAction(model.clear)),
      ));
  }

  facet(f, onAction) {
    const t = theme();
    const trigger = h_flex().flex_none().h(CONTROL.h).px(CONTROL.px).gap(SP.xs).items_center()
      .rounded(RADIUS.control).border_1().border_color(t.border)
      .hover((s) => s.bg(t.hover))
      .child(text("label", f.label))
      .when(f.active, (e) => e.child(text("label", "•", t.foreground)))
      .child(text("label", "▾", t.muted));
    const content = f.key === "show" ? this.showContent(f, onAction) : this.valueContent(f, onAction);
    return Popover.new(`facet-${f.key}`)
      .open(this.open === f.key)
      .on_open_change((open) => {
        if (open) {
          this.open = f.key;
          this.query = "";
          this.search.set_value("");
          if (f.search) this.search.focus();
        } else if (this.open === f.key) {
          this.open = null;
        }
        this.acx.notify();
      })
      .trigger(trigger)
      .content(content);
  }

  showContent(f, onAction) {
    const t = theme();
    const kids = (f.rows ?? []).map((row) => this.flagRow(f, row, onAction));
    if (f.bots) {
      kids.push(div().h(SP.hair).bg(t.border));
      kids.push(this.flagRow(f, {
        key: f.bots.action,
        label: f.bots.label,
        on: f.bots.on,
        action: f.bots.action,
        hint: f.bots.hint,
      }, onAction));
    }
    return popoverSurface().children(kids);
  }

  valueContent(f, onAction) {
    const t = theme();
    return popoverSurface()
      .when(Boolean(f.search), (e) => e.child(
        Input.new(this.search).h(CONTROL.h).px(SP.sm).rounded(RADIUS.control)
          .border_1().border_color(t.input).bg(t.background),
      ))
      .child(v_flex().id(`facet-${f.key}-list`).max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar()
        .children(searchOptions(f.options, this.query).map((o) => this.valueRow(f, o, onAction))));
  }

  flagRow(f, row, onAction) {
    const t = theme();
    return h_flex().min_h(ROW_H.list).px(SP.xs).gap(SP.sm).items_center().rounded(RADIUS.control)
      .hover((s) => s.bg(t.hover))
      .child(Checkbox.new(`fopt-${f.key}-${row.key}`)
        .checked(Boolean(row.on))
        .on_change(() => onAction(row.action))
        .flex_1().min_w(0)
        .child(checkbox(Boolean(row.on)))
        .child(text("label", row.label).flex_1().min_w(0))
        .when(Boolean(row.hint), (e) => e.child(caption(row.hint))));
  }

  valueRow(f, o, onAction) {
    const t = theme();
    const on = o.state === "include";
    const excluded = o.state === "exclude";
    return h_flex().min_h(ROW_H.list).px(SP.xs).gap(SP.sm).items_center().rounded(RADIUS.control)
      .hover((s) => s.bg(t.hover))
      .child(Checkbox.new(`fopt-${f.key}-${o.value}`)
        .checked(on)
        .on_change(() => onAction(o.include))
        .flex_1().min_w(0)
        .child(checkbox(on))
        .child(text("label", o.label, excluded ? t.muted : t.foreground).flex_1().min_w(0)))
      .child(ghostButton(
        `fhide-${f.key}-${o.value}`,
        excluded ? "Unhide" : "Hide",
        () => onAction(o.exclude),
      ));
  }

  tagsRow(tags, onAction) {
    return h_flex().flex_none().flex_wrap().gap(SP.xs).items_center()
      .children(tags.map((tag) => this.filterTag(tag, onAction)));
  }

  filterTag(tag, onAction) {
    const t = theme();
    return h_flex().h(CONTROL.hDense).px(SP.sm).gap(SP.xs).items_center()
      .rounded(RADIUS.tag).border_1().border_color(t.border).cursor_pointer()
      .on_click(() => onAction(tag.action))
      .hover((s) => s.bg(t.hover))
      .child(text("label", tag.text, tag.excluded ? t.muted : t.foreground))
      .child(text("label", "×", t.muted));
  }
}
