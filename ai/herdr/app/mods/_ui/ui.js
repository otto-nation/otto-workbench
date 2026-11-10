// Small shared pieces for team-prs (button-outline, button-solid, tag, inset, segmented control, filter group, eyebrow).
// Tokens live in _lib/tokens.js; colours come from the active theme, never from here. Per the brand's DESIGN.md,
// hover changes a fill and never a border, and t.dim is the readable muted tier (ink-muted), not a border colour.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme, settings } from "herdr";
import { SP, TEXT, RADIUS, CONTROL_H, LAYOUT, WEIGHT } from "../_lib/tokens.js";

// button is outline (hairline, ink text on canvas); solidButton is the one primary action (ink fill, canvas text).
// Both stop the press so only the bare header drags the window.
// A disabled button keeps the shape, uses muted text, and does nothing.
export function button(id, label, onClick, { disabled = false } = {}) {
  const t = theme();
  return div().id(id).px(SP.lg).min_h(CONTROL_H.board).flex().items_center()
    .rounded(RADIUS.control).text_size(TEXT.small).font_semibold()
    .text_color(disabled ? t.muted : t.foreground)
    .border_1().border_color(t.border)
    .when(!disabled, (e) => e.cursor_pointer().hover((s) => s.bg(t.hover))
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
      .on_click(() => onClick()))
    .child(label);
}

export function solidButton(id, label, onClick, { disabled = false } = {}) {
  const t = theme();
  return div().id(id).px(SP.lg).min_h(CONTROL_H.board).flex().items_center()
    .rounded(RADIUS.control).text_size(TEXT.small).font_semibold()
    .border_1()
    .when(disabled, (e) => e.bg(t.border).text_color(t.muted).border_color(t.border))
    .when(!disabled, (e) => e.bg(t.foreground).text_color(t.background).border_color(t.foreground)
      .cursor_pointer().hover((s) => s.bg(t.muted))
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
      .on_click(() => onClick()))
    .child(label);
}

// A small label with a hairline edge and no fill: PR badges, post outcomes, counts.
// DESIGN.md's tag, which stands in for the pills the brand forbids.
export function tag(text, color) {
  const t = theme();
  return div().px(SP.xs).rounded(RADIUS.tag).border_1().border_color(t.border)
    .text_size(TEXT.small).font_weight(WEIGHT.label).text_color(color ?? t.muted).child(text);
}

// A bordered scrolling box for long text inside a pane: review bodies, run output.
export function inset() {
  const t = theme();
  return div().p(SP.md).rounded(RADIUS.control).border_1().border_color(t.border)
    .max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar();
}

// A list row that highlights on hover; callers add on_click and content.
export function hoverRow(id) {
  return div().id(id).rounded(RADIUS.control).cursor_pointer().hover((s) => s.bg(theme().hover));
}
// A bordered, clickable card; callers chain gap, on_click and content.
export function card(id) {
  const t = theme();
  return v_flex().id(id).p(SP.md).rounded(RADIUS.card).border_1().border_color(t.border)
    .cursor_pointer().hover((s) => s.bg(t.hover));
}

// A dim message for an empty or loading state; callers chain padding and size.
export function placeholder(text) {
  return div().text_color(theme().dim).child(text);
}
// A small label; dim unless a colour is given.
export function caption(text, color) {
  return div().text_size(TEXT.caption).text_color(color ?? theme().dim).child(text);
}
// The centered, padded pane for an empty or error state.
export function emptyPane() {
  return v_flex().id("prs").size_full().items_center().justify_center().gap(SP.sm).p(SP.xl)
    .bg(theme().background).text_size(TEXT.body);
}
// The top strip of a pane: hairline below, content stacked.
export function paneBar() {
  return v_flex().flex_none().gap(SP.sm).px(SP.lg).py(SP.md).border_b_1().border_color(theme().border);
}
// The scrolling body of a pane or column.
export function scrollBody() {
  return v_flex().flex_1().min_h(0).overflow_y_scrollbar();
}

// A choice inside a segmented group; the active one is an ink fill with canvas text (DESIGN.md).
export function segment(id, label, active, onClick, size = TEXT.caption) {
  const t = theme();
  return div().id(id).px(SP.sm).min_h(CONTROL_H.compact).flex().items_center().rounded(RADIUS.control)
    .text_size(size).font_medium().cursor_pointer()
    .when(active, (e) => e.bg(t.solid).text_color(t.solid_foreground))
    .when(!active, (e) => e.text_color(t.muted).hover((s) => s.bg(t.hover)))
    .on_click(onClick).child(label);
}

// A row of segments on a hairline-bordered track.
export function segmented(items) {
  const t = theme();
  return h_flex().flex_none().flex_wrap().p(SP.xxs).gap(SP.xxs).rounded(RADIUS.card).border_1().border_color(t.border)
    .children(items);
}

// Independent on/off chip; selected ones get a stronger edge and the selected fill, never fill alone.
export function chip(id, label, on, onClick) {
  const t = theme();
  return div().id(id).px(SP.sm).min_h(CONTROL_H.compact).flex().items_center().rounded(RADIUS.control)
    .text_size(TEXT.caption).font_medium().cursor_pointer().border_1()
    .when(on, (e) => e.border_color(t.dim).bg(t.selected).text_color(t.foreground))
    .when(!on, (e) => e.border_color(t.border).text_color(t.muted).hover((s) => s.bg(t.hover)))
    .on_click(() => onClick()).child(label);
}

export function filterGroup(label, items) {
  return h_flex().gap(SP.sm).items_center()
    .child(caption(label).w(LAYOUT.filterLabelW).flex_none())
    .child(h_flex().flex_1().min_w(0).flex_wrap().gap(SP.xs).items_center().children(items));
}

// Small section label: quiet, semibold (DESIGN.md typography.section), count beside it. strong is the board lane title.
export function eyebrow(label, count, px = SP.md, { strong = false } = {}) {
  const t = theme();
  if (strong) {
    return h_flex().flex_none().px(SP.md).py(SP.md).gap(SP.sm).items_center()
      .child(div().min_w(0).truncate().text_size(TEXT.small).font_semibold().text_color(t.foreground).child(label))
      .child(tag(String(count), count > 0 ? t.foreground : t.dim));
  }
  return h_flex().px(px).pt(SP.md).pb(SP.xs).gap(SP.sm).items_center().flex_none()
    .child(div().text_size(TEXT.caption).font_weight(WEIGHT.section).text_color(t.muted).child(label))
    .when(count != null, (e) => e.child(caption(String(count))));
}

// DESIGN.md glyphs: fail is × (U+00D7), which the brand fonts draw; the ballot X (U+2717) is in none.
const CI = { passing: "✓", failing: "×", pending: "…" };

// The brand's terminal mapping puts danger on ANSI 1 and info on ANSI 6, so those slots carry
// the CI fail and running colours; done is the theme's own success colour.
export function ciMark(status) {
  const t = theme();
  let color = t.dim;
  if (status === "passing") color = t.done;
  else if (status === "failing") color = t.ansi1;
  else if (status === "pending") color = t.ansi6;
  return div().flex_none().text_color(color).child(CI[status] ?? "");
}

export function pane(id) {
  return v_flex().id(id).size_full().min_h(0).bg(theme().background).text_color(theme().foreground);
}

export function mono() {
  return settings().font_family ?? "Menlo";
}

export function addStat(n) {
  return div().text_color(theme().ansi2).child(`+${n}`);
}

export function delStat(n) {
  return div().text_color(theme().ansi1).child(`-${n}`);
}
