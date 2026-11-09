// Small shared pieces for team-prs (segmented control, eyebrow). Tokens live in _lib/tokens.js;
// colours come from the active theme, never from here.
import { div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { theme } from "herdr";
import { SP, TEXT, RADIUS, CONTROL_H } from "./_lib/tokens.js";

// A pill-shaped choice inside a segmented group; the active one lifts onto the selected surface.
export function segment(id, label, active, onClick, size = TEXT.caption) {
  const t = theme();
  return div().id(id).px(SP.sm).h(CONTROL_H.compact).flex().items_center().rounded(RADIUS.control)
    .text_size(size).font_medium().cursor_pointer()
    .when(active, (e) => e.bg(t.selected).text_color(t.foreground))
    .when(!active, (e) => e.text_color(t.muted).hover((s) => s.bg(t.hover)))
    .on_click(onClick).child(label);
}

// A row of segments on a hairline-bordered track.
export function segmented(items) {
  const t = theme();
  return h_flex().flex_none().p(2).gap(2).rounded(RADIUS.control + 2).border_1().border_color(t.border)
    .children(items);
}

// Small section label: quiet, medium weight, count beside it.
export function eyebrow(label, count, px = SP.md) {
  const t = theme();
  return h_flex().px(px).pt(SP.md).pb(SP.xs).gap(SP.sm).items_center().flex_none()
    .child(div().text_size(TEXT.caption).font_medium().text_color(t.muted).child(label))
    .when(count != null, (e) => e.child(div().text_size(TEXT.caption).text_color(t.dim).child(String(count))));
}
