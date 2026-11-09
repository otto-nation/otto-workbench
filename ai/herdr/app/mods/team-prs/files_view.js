// Changed files of a PR and one file's diff, drawn inside the detail pane. Data and caps live in
// _lib/diff.js; this only draws.
import { div } from "gpui-kit";
import { v_flex, h_flex, Scrollbar, v_virtual_list } from "gpui-base";
import { theme } from "herdr";
import { MAX_LINES } from "./_lib/diff.js";
import { mono } from "./detail.js";
import { SP, TEXT, RADIUS } from "./_lib/tokens.js";
import { button } from "./ui.js";

const ROW_H = 20;
// Monospace glyph width is about 0.62 of its size; the gutter holds five digits.
export const CHAR_PX = Math.ceil(TEXT.caption * 0.62);
const GUTTER = CHAR_PX * 5;

function statusColor(t, s) {
  if (s === "A") return t.ansi2;
  if (s === "D") return t.ansi1;
  return t.blocked;
}

function fileRow(f, onPick) {
  const t = theme();
  const parts = f.path.split("/");
  const name = parts.pop();
  return h_flex().id(`file-${f.path}`).h(28).flex_none().px(SP.sm).gap(SP.sm).items_center()
    .rounded(RADIUS.control).cursor_pointer().hover((s) => s.bg(t.hover)).on_click(() => onPick(f.path))
    .child(div().w(14).text_size(TEXT.caption).text_color(statusColor(t, f.status)).child(f.status))
    .child(h_flex().flex_1().min_w(0).when(f.generated, (e) => e.text_color(t.dim))
      .child(div().text_color(t.dim).truncate().child(parts.length ? `${parts.join("/")}/` : ""))
      .child(div().min_w(0).truncate().child(name)))
    .when(f.generated, (e) => e.child(div().text_size(TEXT.caption).text_color(t.dim).child("generated")))
    .when(f.add > 0, (e) => e.child(div().text_size(TEXT.caption).text_color(t.ansi2).child(`+${f.add}`)))
    .when(f.del > 0, (e) => e.child(div().text_size(TEXT.caption).text_color(t.ansi1).child(`-${f.del}`)));
}

// files: null while loading, {ok:false,error} on failure, else {ok:true,files}.
export function renderFileList(files, { onPick }) {
  const t = theme();
  const dim = (s) => div().p(SP.md).text_color(t.dim).child(s);
  const body = (() => {
    if (files == null) return dim("Loading changed files…");
    if (files.ok === false) return dim(files.error?.message ?? "Could not load changed files");
    if (!files.files.length) return dim("No changed files");
    return v_flex().gap(2).children([...files.files.filter((f) => !f.generated), ...files.files.filter((f) => f.generated)]
      .map((f) => fileRow(f, onPick)));
  })();
  return v_flex().id("prs-files").flex_1().min_h(0).overflow_y_scrollbar().px(SP.lg).pb(SP.lg).child(body);
}

function diffLine({ kind, number, text }) {
  const t = theme();
  if (kind === "@") {
    return div().w_full().px(SP.md).h(ROW_H).flex_none().truncate().bg(t.raised).text_color(t.dim).child(text);
  }
  return h_flex().w_full().h(ROW_H).flex_none().gap(SP.md)
    .when(kind === "+", (e) => e.bg(`${t.ansi2}18`)).when(kind === "-", (e) => e.bg(`${t.ansi1}18`))
    .child(div().w(GUTTER).flex_none().text_right().text_color(t.dim).child(number === undefined ? "" : String(number)))
    .child(div().flex_1().min_w(0).truncate().child(text));
}

// One file's diff as a virtual list of fixed-height rows; `diff` is patchRows' result.
export function renderDiff(file, diff, { onBack }) {
  const t = theme();
  const note = file.patch == null ? "No text diff for this file (binary or too large)" : null;
  return v_flex().id("prs-diff").flex_1().min_h(0)
    .child(h_flex().flex_none().gap(SP.sm).items_center().px(SP.lg).py(SP.sm).border_b_1().border_color(t.border)
      .child(button("diff-back", "← Files", onBack))
      .child(div().flex_1().min_w(0).truncate().text_size(TEXT.small).child(file.path)))
    .when(Boolean(note), (e) => e.child(div().p(SP.lg).text_color(t.dim).child(note)))
    .when(!note, (e) => e.child(div().flex_1().min_h(0).relative().font_family(mono()).text_size(TEXT.caption)
      .child(div().id("diff-x").size_full().overflow_x_scrollbar()
        .child(div().w_full().min_w(diff.width).h_full().child(v_virtual_list("pr-diff", diff.rows.length, ROW_H,
          (i) => String(i), (range) => diff.rows.slice(range.start, range.end).map(diffLine)).size_full())))
      .child(Scrollbar.vertical("pr-diff").viewport_from_layout().absolute().inset_0())))
    .when(diff.truncated, (e) => e.child(div().flex_none().px(SP.lg).py(SP.xs).text_size(TEXT.caption)
      .text_color(t.dim).child(`Showing the first ${MAX_LINES.toLocaleString("en-US")} lines`)));
}
