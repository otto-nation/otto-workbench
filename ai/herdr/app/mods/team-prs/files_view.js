// Changed files of a PR and one file's diff, drawn inside the detail pane. Data and caps live in
// _lib/diff.js; this only draws.
import { div } from "gpui-kit";
import { v_flex, h_flex, Scrollbar, v_virtual_list } from "gpui-base";
import { theme } from "herdr";
import { MAX_LINES } from "./_lib/diff.js";
import { SP, TEXT, ROW_H, LAYOUT, CONTROL_H, FILL, MONO } from "./_lib/tokens.js";
import { button, addStat, delStat, hoverRow, placeholder, caption, scrollBody, mono } from "./ui.js";

function statusColor(t, s) {
  if (s === "A") return t.ansi2;
  if (s === "D") return t.ansi1;
  return t.blocked;
}

function fileRow(f, onPick) {
  const t = theme();
  const parts = f.path.split("/");
  const name = parts.pop();
  return hoverRow(`file-${f.path}`).h(CONTROL_H.board).flex_none().px(SP.sm).gap(SP.sm).items_center()
    .on_click(() => onPick(f.path))
    .child(div().w(LAYOUT.statusW).text_size(TEXT.caption).text_color(statusColor(t, f.status)).child(f.status))
    .child(h_flex().flex_1().min_w(0).when(f.generated, (e) => e.text_color(t.dim))
      .child(div().text_color(t.dim).truncate().child(parts.length ? `${parts.join("/")}/` : ""))
      .child(div().min_w(0).truncate().child(name)))
    .when(f.generated, (e) => e.child(caption("generated")))
    .when(f.add > 0, (e) => e.child(addStat(f.add).text_size(TEXT.caption)))
    .when(f.del > 0, (e) => e.child(delStat(f.del).text_size(TEXT.caption)));
}

// files: null while loading, {ok:false,error} on failure, else {ok:true,files}.
export function renderFileList(files, { onPick }) {
  const dim = (s) => placeholder(s).p(SP.md);
  const body = (() => {
    if (files == null) return dim("Loading changed files…");
    if (files.ok === false) return dim(files.error?.message ?? "Could not load changed files");
    if (!files.files.length) return dim("No changed files");
    return v_flex().gap(SP.xxs).children([...files.files.filter((f) => !f.generated), ...files.files.filter((f) => f.generated)]
      .map((f) => fileRow(f, onPick)));
  })();
  return scrollBody().id("prs-files").px(SP.lg).pb(SP.lg).child(body);
}

function diffLine({ kind, number, text }) {
  const t = theme();
  if (kind === "@") {
    return div().w_full().px(SP.md).h(ROW_H.diff).flex_none().truncate().bg(t.raised).text_color(t.dim).child(text);
  }
  return h_flex().w_full().h(ROW_H.diff).flex_none().gap(SP.md)
    .when(kind === "+", (e) => e.bg(`${t.ansi2}${FILL.add}`)).when(kind === "-", (e) => e.bg(`${t.ansi1}${FILL.del}`))
    .child(div().w(MONO.gutter).flex_none().text_right().text_color(t.dim).child(number === undefined ? "" : String(number)))
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
    .when(Boolean(note), (e) => e.child(placeholder(note).p(SP.lg)))
    .when(!note, (e) => e.child(div().flex_1().min_h(0).relative().font_family(mono()).text_size(TEXT.caption)
      .child(div().id("diff-x").size_full().overflow_x_scrollbar()
        .child(div().w_full().min_w(diff.width).h_full().child(v_virtual_list("pr-diff", diff.rows.length, ROW_H.diff,
          (i) => String(i), (range) => diff.rows.slice(range.start, range.end).map(diffLine)).size_full())))
      .child(Scrollbar.vertical("pr-diff").viewport_from_layout().absolute().inset_0())))
    .when(diff.truncated, (e) => e.child(div().flex_none().px(SP.lg).py(SP.xs).text_size(TEXT.caption)
      .text_color(t.dim).child(`Showing the first ${MAX_LINES.toLocaleString("en-US")} lines`)));
}
