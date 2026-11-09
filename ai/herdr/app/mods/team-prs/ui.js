// Small shared pieces for team-prs (button, pill, inset, segmented control, filter group, eyebrow). Tokens live in _lib/tokens.js;
// colours come from the active theme, never from here.
import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme, settings } from "herdr";
import { SP, TEXT, RADIUS, CONTROL_H, LAYOUT } from "./_lib/tokens.js";

// Buttons stop the press so only the bare header drags the window.
// A disabled button keeps the shape but is dim and does nothing.
export function button(id, label, onClick, { disabled = false } = {}) {
  const t = theme();
  return div().id(id).px(SP.sm).min_h(CONTROL_H.board).flex().items_center()
    .rounded(RADIUS.control).text_size(TEXT.caption)
    .border_1().border_color(t.border)
    .when(disabled, (e) => e.text_color(t.dim))
    .when(!disabled, (e) => e.cursor_pointer().hover((s) => s.bg(t.hover).border_color(t.dim))
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
      .on_click(() => onClick()))
    .child(label);
}

// A small bordered label: PR badges, post outcomes.
export function pill(text, color) {
  const t = theme();
  return div().px(SP.sm).py(SP.hair).rounded(RADIUS.control).bg(t.background).border_1().border_color(t.border)
    .text_size(TEXT.caption).text_color(color ?? t.muted).child(text);
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
    .cursor_pointer().hover((s) => s.bg(t.hover).border_color(t.dim));
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

// A pill-shaped choice inside a segmented group; the active one lifts onto the selected surface.
export function segment(id, label, active, onClick, size = TEXT.caption) {
  const t = theme();
  return div().id(id).px(SP.sm).min_h(CONTROL_H.compact).flex().items_center().rounded(RADIUS.control)
    .text_size(size).font_medium().cursor_pointer()
    .when(active, (e) => e.bg(t.selected).text_color(t.foreground))
    .when(!active, (e) => e.text_color(t.muted).hover((s) => s.bg(t.hover)))
    .on_click(onClick).child(label);
}

// A row of segments on a hairline-bordered track.
export function segmented(items) {
  const t = theme();
  return h_flex().flex_none().flex_wrap().p(SP.xxs).gap(SP.xxs).rounded(RADIUS.card).border_1().border_color(t.border)
    .children(items);
}

// Independent on/off chip; selected ones get a dim border and selected fill.
export function chip(id, label, on, onClick) {
  const t = theme();
  return div().id(id).px(SP.sm).min_h(CONTROL_H.compact).flex().items_center().rounded(RADIUS.control)
    .text_size(TEXT.caption).font_medium().cursor_pointer().border_1()
    .when(on, (e) => e.border_color(t.dim).bg(t.selected).text_color(t.foreground))
    .when(!on, (e) => e.border_color(t.border).text_color(t.muted).hover((s) => s.bg(t.hover).border_color(t.dim)))
    .on_click(() => onClick()).child(label);
}

export function filterGroup(label, items) {
  return h_flex().gap(SP.sm).items_center()
    .child(caption(label).w(LAYOUT.filterLabelW).flex_none())
    .child(h_flex().flex_1().min_w(0).flex_wrap().gap(SP.xs).items_center().children(items));
}

// Small section label: quiet, medium weight, count beside it. strong is the board lane title.
export function eyebrow(label, count, px = SP.md, { strong = false } = {}) {
  const t = theme();
  if (strong) {
    return h_flex().flex_none().px(SP.md).py(SP.md).gap(SP.sm).items_center()
      .child(div().min_w(0).truncate().text_size(TEXT.small).font_semibold().text_color(t.foreground).child(label))
      .child(pill(String(count), count > 0 ? t.foreground : t.dim));
  }
  return h_flex().px(px).pt(SP.md).pb(SP.xs).gap(SP.sm).items_center().flex_none()
    .child(div().text_size(TEXT.caption).font_medium().text_color(t.muted).child(label))
    .when(count != null, (e) => e.child(caption(String(count))));
}

const CI = { passing: "✓", failing: "✗", pending: "…" };

export function ciMark(status) {
  const t = theme();
  let color = t.dim;
  if (status === "passing") color = t.ansi2;
  else if (status === "failing") color = t.ansi1;
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
