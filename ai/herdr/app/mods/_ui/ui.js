// Shared pieces for the mods, each one a DESIGN.md component: buttons (outline, solid, ghost,
// icon), checkbox, checkRow, popover surface, tag, status glyph, section label, segmented control,
// card, list row. Sizes come from _lib/tokens.js; colours come from the active theme, never from here.
import { div, svg } from "gpui-kit";
import { h_flex, v_flex, TextView, Checkbox } from "gpui-base";
import { theme, settings } from "herdr";
import { SP, TYPE, RADIUS, CONTROL, LAYOUT, ROW_H } from "../_lib/tokens.js";
import { STATUS, DIFF, statusOf } from "../_lib/status.js";

export function mono() {
  return settings().font_family ?? "Menlo";
}

// Applies a DESIGN.md typography role (TYPE key) to an element. The line height goes in as a px
// string: a bare number is read as a multiple of the font size.
export function type(el, role) {
  const r = TYPE[role] ?? TYPE.body;
  let out = el.text_size(r.size).line_height(`${r.lh}px`);
  out = out.font_weight(r.weight);
  return r.mono ? out.font_family(mono()) : out;
}

// A line of text in a role and colour; ink unless a colour is given.
export function text(role, content, color) {
  return type(div(), role).text_color(color ?? theme().foreground).child(content);
}

// Controls stop the press so only the bare header drags the window.
function control(id, { disabled, onClick }) {
  const el = h_flex().id(id).flex_none().h(CONTROL.h).items_center().justify_center().rounded(RADIUS.control);
  if (disabled) return el;
  return el.cursor_pointer()
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .on_click(() => onClick());
}

// button-outline: hairline border on canvas, ink label; hover fills, the border stays.
// Disabled: muted label, no hover.
export function button(id, label, onClick, { disabled = false } = {}) {
  const t = theme();
  return type(control(id, { disabled, onClick }), "label").px(CONTROL.px)
    .border_1().border_color(t.border)
    .text_color(disabled ? t.muted : t.foreground)
    .when(!disabled, (e) => e.hover((s) => s.bg(t.hover)))
    .child(label);
}

// button-solid, the one primary action: ink fill, canvas label. Disabled keeps the fill at half
// opacity, since a muted label on ink does not read.
export function solidButton(id, label, onClick, { disabled = false } = {}) {
  const t = theme();
  return type(control(id, { disabled, onClick }), "label").px(CONTROL.px)
    .bg(t.solid).text_color(t.solid_foreground)
    .when(disabled, (e) => e.opacity(0.5))
    .child(label);
}

// Ghost: label type, control padding, no border. Hover fills on its own line.
export function ghostButton(id, label, onClick) {
  const t = theme();
  return type(control(id, { onClick }), "label").px(CONTROL.px)
    .text_color(t.foreground)
    .hover((s) => s.bg(t.hover))
    .child(label);
}

// ceiling: refresh keeps glyph.spin(900); once gpui-kit exposes reduce-motion, skip spin.
// A square ghost button holding an svg (tinted with the text colour). `label` is its tooltip and
// accessible name; `spinning` turns the icon while work runs.
export function iconButton(id, icon, label, onClick, { disabled = false, spinning = false } = {}) {
  const t = theme();
  const glyph = svg(icon).size(LAYOUT.iconSize);
  return control(id, { disabled, onClick }).w(CONTROL.h)
    .text_color(disabled ? t.muted : t.foreground)
    .tooltip(label).accessibility_label(label)
    .when(!disabled, (e) => e.hover((s) => s.bg(t.hover)))
    .child(spinning ? glyph.spin(900) : glyph);
}

// tag: a label that needs a container (PR labels, a machine name). Hairline, no fill, muted.
export function tag(content) {
  const t = theme();
  return type(div(), "label").flex_none().px(SP.xs).rounded(RADIUS.tag).border_1().border_color(t.border)
    .text_color(t.muted).child(content);
}

// Status is coloured text plus a glyph, never a pill. Without `label` the glyph stands alone and
// `aria` names it.
export function statusText(kind, label, { role = "meta", aria, gap = SP.xxs } = {}) {
  const s = statusOf(kind);
  const color = theme()[s.field];
  const row = type(h_flex(), role).flex_none().gap(gap).items_center().text_color(color)
    .child(div().child(s.glyph));
  if (label) return row.child(div().child(label));
  return row.accessibility_label(aria ?? kind);
}

// The count slot of a lane or section heading: the count, or while the lane's data is still loading
// or failed, DESIGN.md's static status glyph (… running, × fail) named by the message.
function countSlot(count, { status = 'ready', message = null } = {}) {
  if (status === 'loading') return statusText('running', null, { aria: message ?? 'Loading' });
  if (status === 'error') return statusText('fail', null, { aria: message ?? 'Failed' });
  return count == null ? null : text('meta', String(count), theme().muted);
}

// section-label: sentence case, muted; a count beside it is meta.
export function sectionLabel(label, count = null, state = {}) {
  const t = theme();
  const slot = countSlot(count, state);
  return h_flex().flex_none().gap(SP.sm).items_center()
    .child(text("section", label, t.muted))
    .when(slot != null, (e) => e.child(slot));
}

// A page's section heading: the title role in ink, where sectionLabel is a muted 11px label.
// Trialled on the Batch page, where sections are the page's structure rather than labels.
export function sectionHeading(label, count = null) {
  const t = theme();
  return h_flex().flex_none().gap(SP.sm).items_center().pb(SP.xs).border_b_1().border_color(t.border)
    .child(text("title", label))
    .when(count != null, (e) => e.child(text("meta", String(count), t.muted)));
}

// A bordered scrolling box for long text inside a pane: review bodies, run output.
// `maxH` lets a caller scale the box with its window; the default is the fixed token.
// `scroll: false` leaves scrolling to a child that scrolls itself (a scrollable TextView).
export function inset({ maxH = LAYOUT.bodyMaxH, scroll = true } = {}) {
  const t = theme();
  return div().p(SP.md).rounded(RADIUS.control).border_1().border_color(t.border)
    .max_h(maxH).when(scroll, (e) => e.overflow_y_scrollbar());
}

// list-row: hover fills; selected adds the selected fill, a 2px primary rule on the left and ink
// text, so fill is never the only cue. Callers add on_click and content.
export function hoverRow(id, { selected = false } = {}) {
  const t = theme();
  return div().id(id).rounded(RADIUS.control).cursor_pointer()
    .when(selected, (e) => e.bg(t.selected).border_l_2().border_color(t.accent).text_color(t.foreground))
    .when(!selected, (e) => e.hover((s) => s.bg(t.hover)));
}

// card-linked: hairline card on canvas; hover fills, the border stays. Callers chain gap, on_click
// and content.
export function card(id) {
  const t = theme();
  return v_flex().id(id).p(SP.md).rounded(RADIUS.card).border_1().border_color(t.border)
    .cursor_pointer().hover((s) => s.bg(t.hover));
}

// The empty and loading state: muted body text ("Loading…", "No pull requests").
export function placeholder(content) {
  return text("body", content, theme().muted);
}

// DESIGN.md's error state: a one-line reason in the danger colour. Callers that can reload add
// an outline "Retry" button beside it.
export const dangerColor = () => theme()[STATUS.fail.field];

export function errorLine(content) {
  return text("body", content, dangerColor());
}

// Meta-sized supporting text; muted unless a colour is given.
export function caption(content, color) {
  return text("meta", content, color ?? theme().muted);
}

// The centred, padded pane for an empty or error state.
export function emptyPane() {
  return type(v_flex().id("prs").size_full().items_center().justify_center().gap(SP.sm).p(SP.xl)
    .bg(theme().background), "body");
}

// A header that stays put while the content under it scrolls: a hairline below, always on, never a
// shadow (DESIGN.md). Every pinned header goes through here so the rule cannot be forgotten.
export function pinned(el) {
  return el.flex_none().border_b_1().border_color(theme().border);
}

// The top strip of a pane: hairline below, content stacked.
export function paneBar() {
  return pinned(v_flex().gap(SP.sm).px(SP.lg).py(SP.md));
}

// Selectable, scrollable code: a fenced block in a TextView, so it can be highlighted and copied.
// The fence is longer than any backtick run in the lines so the text cannot close it early. `id` is
// stable per place, so the scroll position survives a refresh.
export function selectableCode(id, lines, maxH) {
  const body = lines.join("\n");
  const longest = Math.max(0, ...(body.match(/`+/g) ?? []).map((r) => r.length));
  const fence = "`".repeat(Math.max(3, longest + 1));
  return TextView.markdown(id, `${fence}\n${body}\n${fence}`).selectable().scrollable().max_h(maxH);
}

// The scrolling body of a pane or column.
export function scrollBody() {
  return v_flex().flex_1().min_h(0).overflow_y_scrollbar();
}

// One choice in a segmented control: muted label, or ink fill with canvas label when selected.
export function segment(id, label, active, onClick) {
  const t = theme();
  return type(div().id(id).px(CONTROL.px).h(CONTROL.hDense).flex().items_center().rounded(RADIUS.control), "label")
    .cursor_pointer()
    .when(active, (e) => e.bg(t.solid).text_color(t.solid_foreground))
    .when(!active, (e) => e.text_color(t.muted).hover((s) => s.bg(t.hover)))
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .on_click(onClick).child(label);
}

// segmented: the hairline track holding the segments, control height overall.
export function segmented(items) {
  const t = theme();
  return h_flex().flex_none().h(CONTROL.h).items_center().px(SP.xxs).gap(SP.xxs)
    .rounded(RADIUS.control).border_1().border_color(t.border)
    .children(items);
}

// Checkbox indicator: outline edge, tag radius, SP.lg. Checked is ring fill and a canvas tick,
// never the accent colour.
export function checkbox(on) {
  const t = theme();
  return type(div().flex_none().size(SP.lg).flex().items_center().justify_center()
    .rounded(RADIUS.tag).border_1().border_color(t.input).bg(t.background), "meta")
    .when(on, (e) => e.bg(t.ring).border_color(t.ring).text_color(t.background).child("✓"));
}

// A filter/option row: checkbox indicator and truncated label on one line, optional hint or
// meta count, optional trailing control (Hide/Unhide). Hover fills; the border does not change.
// gpui-base Checkbox is an unstyled block, so the indicator and label live in an h_flex child
// rather than as stacked Checkbox children.
export function checkRow(id, { on, label, hint, count, muted, onToggle, trailing } = {}) {
  const t = theme();
  const ink = muted ? t.muted : t.foreground;
  return h_flex().min_h(ROW_H.list).px(SP.xs).gap(SP.sm).items_center().rounded(RADIUS.control)
    .hover((s) => s.bg(t.hover))
    .child(
      Checkbox.new(id)
        .checked(Boolean(on))
        .on_change(() => onToggle?.())
        .flex_1().min_w(0)
        .child(
          h_flex().items_center().gap(SP.sm).flex_1().min_w(0)
            .child(checkbox(Boolean(on)))
            .child(text("label", label, ink).flex_1().min_w(0).truncate())
            .when(Boolean(hint), (e) => e.child(caption(hint)))
            .when(count != null, (e) => e.child(caption(String(count)))),
        ),
    )
    .when(trailing != null, (e) => e.child(trailing));
}

// Anchored menu surface: popover fill, input edge, card radius and padding.
export function popoverSurface() {
  const t = theme();
  return v_flex().p(SP.md).gap(SP.xxs).rounded(RADIUS.card)
    .border_1().border_color(t.input).bg(t.popover).text_color(t.foreground)
    .min_w(LAYOUT.colMin);
}

// A board lane's heading: title in ink with its count in meta beside it, and an optional
// control at the far end.
export function laneTitle(label, count = null, action = null, state = {}) {
  const slot = countSlot(count, state);
  return h_flex().flex_none().px(SP.md).py(SP.md).gap(SP.sm).items_center()
    .child(text("title", label).min_w(0).truncate())
    .when(slot != null, (e) => e.child(slot))
    .when(Boolean(action), (e) => e.child(div().flex_1()).child(action));
}

export function pane(id) {
  return v_flex().id(id).size_full().min_h(0).bg(theme().background).text_color(theme().foreground);
}

export function addStat(n) {
  return div().text_color(theme()[DIFF.add]).child(`+${n}`);
}

export function delStat(n) {
  return div().text_color(theme()[DIFF.del]).child(`-${n}`);
}

// One presentation badge (_lib/pr_status.js): status text, a tag, or muted meta text.
export function badge(b) {
  if (b.kind === "status") return statusText(b.status, b.text);
  if (b.kind === "tag") return tag(b.text);
  return text("meta", b.text, theme().muted);
}
// A wrapping row of badges: a card's badge row, the detail's status row.
export function badgeRow(list) {
  return h_flex().gap_x(SP.xs).gap_y(SP.xs).flex_wrap().items_center().children(list.map(badge));
}
// A presentation's stats (+N, -N, N files) in meta, coloured by DIFF.
export function statsRow(stats) {
  const t = theme();
  return h_flex().gap(SP.sm).items_center()
    .child(text("meta", stats.additions, t[DIFF.add]))
    .child(text("meta", stats.deletions, t[DIFF.del]))
    .child(text("meta", stats.files, t[DIFF.mod]));
}
