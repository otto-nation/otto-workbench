import { div } from "gpui-kit";
import { v_flex, h_flex, TextView } from "gpui-base";
import { theme } from "herdr";
import { historyRows } from "./_lib/history.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };

// Buttons stop the press so only the bare header drags the window.
function button(id, label, onClick) {
  const t = theme();
  return div().id(id).px(8).py(4).rounded(6).text_size(11).cursor_pointer()
    .border_1().border_color(t.border).hover((s) => s.bg(t.hover))
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .on_click(() => onClick()).child(label);
}

function reviewBody(stamp, body) {
  const t = theme();
  const dim = (s) => div().text_color(t.dim).child(s);
  if (body == null) return dim("loading…");
  if (body.ok === false) return dim(body.error?.message ?? "");
  if (body.text == null) return dim("review file not found — it may have been pruned");
  return TextView.markdown("review-" + stamp, body.text).selectable().scrollable();
}

export function prUrl(d) {
  return d.url || `https://${d.host || "github.com"}/${d.repo}/pull/${d.number}`;
}

export function renderDetailHeader(d, { onClose, onOpenUrl }) {
  const t = theme();
  return v_flex().flex_none().gap(6).p(10).border_b_1().border_color(t.border)
    .child(h_flex().gap(8).items_center()
      .child(div().flex_1().min_w(0).truncate().font_semibold().child(`#${d.number} ${d.title ?? ""}`))
      .child(button("detail-open", "View on GitHub", () => onOpenUrl(prUrl(d))))
      .child(button("detail-close", "×", () => onClose())))
    .child(h_flex().gap(8).items_center().text_size(11).text_color(t.dim)
      .child(div().child(d.author ?? "")).child(div().truncate().child(d.branch ?? ""))
      .child(div().text_color(t.ansi2).child(`+${d.additions ?? 0}`))
      .child(div().text_color(t.ansi1).child(`-${d.deletions ?? 0}`))
      .child(div().child(`${d.files ?? 0} files`))
      .child(div().child(CI[d.ci] ?? ""))
      .child(div().child(d.decision ? String(d.decision).toLowerCase().replace(/_/g, " ") : "")));
}

export function renderDetail(d, { onClose, onOpenUrl, nowMs, openStamp, bodyFor, onToggle }) {
  const t = theme();
  const rows = historyRows(d.runs ?? [], nowMs);
  return v_flex().id("prs-detail").size_full().min_h(0).bg(t.background).text_color(t.foreground).text_size(13)
    .child(renderDetailHeader(d, { onClose, onOpenUrl }))
    .child(div().px(10).py(6).flex_none().text_color(t.muted).child("Review history"))
    .child(v_flex().flex_1().min_h(0).overflow_y_scrollbar().px(10).gap(6)
      .when(!rows.length, (e) => e.child(div().text_color(t.dim).child("No reviews yet")))
      .children(rows.map((r) => v_flex().id(`run-${r.stamp}`).p(8).gap(2).rounded(8).border_1()
        .border_color(t.border).cursor_pointer().on_click(() => onToggle(r.stamp))
        .child(h_flex().gap(8).child(div().font_semibold().child(r.verdict || "review"))
          .child(div().text_color(t.dim).child(r.when)).child(div().text_color(t.dim).child(r.shortSha)))
        .child(div().text_size(11).child(r.findingsText))
        .child(div().text_size(11).text_color(t.dim).child(`${r.post.label}${r.costText ? " · " + r.costText : ""}`))
        .when(openStamp === r.stamp, (e) => e.child(reviewBody(r.stamp, bodyFor(r.stamp)))))));
}
