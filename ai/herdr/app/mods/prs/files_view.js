// Changed files of a PR and one file's diff, drawn inside the detail pane. Data and caps live in
// _lib/diff.js; this only draws.
import { div } from "gpui-kit";
import { v_flex, h_flex, Scrollbar, v_virtual_list } from "gpui-base";
import { theme } from "herdr";
import { MAX_LINES } from "./_lib/diff.js";
import { SP, ROW_H, LAYOUT, CONTROL, FILL, MONO } from "./_lib/tokens.js";
import { button, addStat, delStat, hoverRow, placeholder, caption, scrollBody, type, text, errorLine } from "./_ui/ui.js";

function fileMarkColor(t, s) {
  if (s === "A") return t.done;
  if (s === "D") return t.ansi1;
  return t.muted;
}

function fileRow(f, onPick) {
  const t = theme();
  const parts = f.path.split("/");
  const name = parts.pop();
  const ink = f.generated ? t.muted : t.foreground;
  return hoverRow(`file-${f.path}`).flex().h(CONTROL.h).flex_none().px(SP.sm).gap(SP.sm).items_center()
    .on_click(() => onPick(f.path))
    .child(type(div().w(LAYOUT.statusW).text_color(fileMarkColor(t, f.status)), "code").child(f.status))
    .child(type(h_flex().flex_1().min_w(0).text_color(ink), "code")
      .child(text("code", parts.length ? `${parts.join("/")}/` : "", t.muted).truncate())
      .child(type(div().min_w(0).truncate(), "code").child(name)))
    .when(f.generated, (e) => e.child(caption("generated")))
    .when(f.add > 0, (e) => e.child(type(addStat(f.add), "meta")))
    .when(f.del > 0, (e) => e.child(type(delStat(f.del), "meta")));
}

// files: null while loading, {ok:false,error} on failure, else {ok:true,files}.
export function renderFileList(files, { onPick }) {
  const dim = (s) => placeholder(s).p(SP.md);
  const body = (() => {
    if (files == null) return dim("Loading changed files…");
    if (files.ok === false) return dim(files.error?.message ?? "Could not load changed files");
    if (!files.files.length && !files.loading) return dim("No changed files");
    const rows = [...files.files.filter((f) => !f.generated), ...files.files.filter((f) => f.generated)]
      .map((f) => fileRow(f, onPick));
    if (files.loading) {
      const n = Math.max(1, (files.total ?? 0) - files.files.length);
      rows.push(dim(`Loading ${n} more files…`));
    }
    if (files.pageError) rows.push(errorLine(files.pageError));
    return v_flex().gap(SP.xxs).children(rows);
  })();
  return scrollBody().id("prs-files").px(SP.lg).pb(SP.lg).child(body);
}

function diffLine({ kind, number, text: line }) {
  const t = theme();
  if (kind === "@") {
    return type(div().w_full().px(SP.md).h(ROW_H.diff).flex_none().truncate().bg(t.raised).text_color(t.muted), "code")
      .child(line);
  }
  return type(h_flex().w_full().h(ROW_H.diff).flex_none().gap(SP.md), "code")
    .when(kind === "+", (e) => e.bg(`${t.ansi2}${FILL.add}`)).when(kind === "-", (e) => e.bg(`${t.ansi1}${FILL.del}`))
    .child(type(div().w(MONO.gutter).flex_none().text_right().text_color(t.muted), "code")
      .child(number === undefined ? "" : String(number)))
    .child(type(div().flex_1().min_w(0).truncate(), "code").child(line));
}

// One file's diff as a virtual list of fixed-height rows; `diff` is patchRows' result.
export function renderDiff(file, diff, { onBack }) {
  const t = theme();
  const note = file.loadingPatch ? "Loading diff…"
    : file.patchError ? file.patchError
    : file.patch == null ? "No text diff for this file (binary or too large)" : null;
  return v_flex().id("prs-diff").flex_1().min_h(0)
    .child(h_flex().flex_none().gap(SP.sm).items_center().px(SP.lg).py(SP.sm).border_b_1().border_color(t.border)
      .child(button("diff-back", "← Files", onBack))
      .child(type(div().flex_1().min_w(0).truncate(), "code").child(file.path)))
    .when(Boolean(note), (e) => e.child(placeholder(note).p(SP.lg)))
    .when(!note, (e) => e.child(type(div().flex_1().min_h(0).relative(), "code")
      .child(div().id("diff-x").size_full().overflow_x_scrollbar()
        .child(div().w_full().min_w(diff.width).h_full().child(v_virtual_list("pr-diff", diff.rows.length, ROW_H.diff,
          (i) => String(i), (range) => diff.rows.slice(range.start, range.end).map(diffLine)).size_full())))
      .child(Scrollbar.vertical("pr-diff").viewport_from_layout().absolute().inset_0())))
    .when(Boolean(diff?.truncated), (e) => e.child(caption(`Showing the first ${MAX_LINES.toLocaleString("en-US")} lines`)
      .flex_none().px(SP.lg).py(SP.xs)));
}
