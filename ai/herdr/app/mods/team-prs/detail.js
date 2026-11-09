import { div } from "gpui-kit";
import { v_flex, h_flex, TextView } from "gpui-base";
import { theme } from "herdr";
import { historyRows } from "./_lib/history.js";
import { tailLines } from "./_lib/output.js";
import { SP, TEXT, RADIUS, CONTROL_H, LAYOUT } from "./_lib/tokens.js";
import { eyebrow } from "./ui.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };

// Buttons stop the press so only the bare header drags the window.
function button(id, label, onClick) {
  const t = theme();
  return div().id(id).px(SP.sm).h(CONTROL_H.board).flex().items_center()
    .rounded(RADIUS.control).text_size(TEXT.caption).cursor_pointer()
    .border_1().border_color(t.border).hover((s) => s.bg(t.hover).border_color(t.dim))
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .on_click(() => onClick()).child(label);
}

function postTone(tone) {
  const t = theme();
  if (tone === "success") return t.ansi2;
  if (tone === "warning") return t.ansi1;
  if (tone === "info") return t.accent;
  return t.dim;
}

function reviewBody(stamp, body) {
  const t = theme();
  const dim = (s) => div().text_color(t.dim).child(s);
  if (body == null) return dim("loading…");
  if (body.ok === false) return dim(body.error?.message ?? "");
  if (body.text == null) return dim("review file not found — it may have been pruned");
  return TextView.markdown("review-" + stamp, body.text).selectable();
}

function reviewInset(stamp, body) {
  const t = theme();
  return div().mt(SP.sm).p(SP.md).rounded(RADIUS.control).border_1().border_color(t.border)
    .max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar().child(reviewBody(stamp, body));
}

// Enabled actions as buttons; when none is enabled, the first reason says why. A post action
// asks first (confirm), because it writes to GitHub.
export function renderActionBar(actions, { confirm, note, onAction, onConfirm, onCancel }) {
  const t = theme();
  if (confirm) {
    const c = confirm.confirm;
    return v_flex().flex_none().gap(SP.sm).px(SP.lg).py(SP.md).border_b_1().border_color(t.border)
      .child(div().text_size(TEXT.small).child(
        `${confirm.label} on ${c.pr}? Posts the review of ${c.sha} to GitHub.`))
      .child(h_flex().gap(SP.sm).child(button("act-confirm", `Confirm: ${confirm.label}`, onConfirm))
        .child(button("act-cancel", "Cancel", onCancel)));
  }
  const on = actions.filter((a) => a.enabled);
  const why = actions.find((a) => !a.enabled && a.id !== "cancel" && a.id !== "watch");
  return h_flex().flex_none().flex_wrap().gap(SP.sm).items_center().px(SP.lg).py(SP.md)
    .border_b_1().border_color(t.border)
    .children(on.map((a) => button(`act-${a.id}`, a.label, () => onAction(a))))
    .when(!on.length && Boolean(why), (e) => e.child(
      div().text_size(TEXT.caption).text_color(t.dim).child(why.reason)))
    .when(Boolean(note), (e) => e.child(div().text_size(TEXT.caption).text_color(t.ansi1).child(note)));
}

// What the detail view needs from a board card.
export function detailOf(pr) {
  return {
    repo: pr.repoSlug, host: pr.host, number: pr.number, title: pr.title, author: pr.author,
    branch: pr.branch, ci: pr.ciStatus, decision: pr.reviewDecision, additions: pr.additions,
    deletions: pr.deletions, files: pr.changedFiles, url: pr.url, on: pr.on ?? null,
    runs: (pr.reviewRuns ?? []).map((r) => r.run),
    cwd: pr.cwd ?? null, headMoved: Boolean(pr.hasNewChangesSinceReview),
    batchBusy: Boolean(pr.busy), live: pr.reviewTerminal ?? null,
  };
}

// The run's text, read-only, inside the pane. The terminal itself can open as a tab for anything
// interactive.
export function renderOutput(output, { onHide, onTab }) {
  const t = theme();
  const lines = tailLines(output.text ?? "", 80);
  return v_flex().flex_none().gap(SP.sm).px(SP.lg).py(SP.md).border_b_1().border_color(t.border)
    .child(h_flex().gap(SP.sm).items_center().justify_between()
      .child(div().text_size(TEXT.caption).text_color(t.dim).child(output.live ? "Run output · live" : "Run output"))
      .child(h_flex().gap(SP.sm)
        .child(button("out-tab", "Open as tab", onTab))
        .child(button("out-hide", "Hide", onHide))))
    .child(v_flex().p(SP.md).rounded(RADIUS.control).border_1().border_color(t.border)
      .max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar().text_size(TEXT.caption).font_family("Menlo")
      .when(output.text == null, (e) => e.child(div().text_color(t.dim).child("loading…")))
      .when(output.text != null && !lines.length, (e) => e.child(div().text_color(t.dim).child("no output yet")))
      .children(lines.map((l) => div().child(l === "" ? " " : l))));
}

export function prUrl(d) {
  return d.url || `https://${d.host || "github.com"}/${d.repo}/pull/${d.number}`;
}

export function renderDetailHeader(d, { onClose, onOpenUrl }) {
  const t = theme();
  return v_flex().flex_none().gap(SP.sm).p(SP.lg).border_b_1().border_color(t.border)
    .child(h_flex().gap(SP.sm).items_center().justify_between()
      .child(button("detail-back", "← Back", () => onClose()))
      .child(button("detail-open", "View on GitHub", () => onOpenUrl(prUrl(d)))))
    .child(div().min_w(0).text_size(TEXT.title).font_semibold().max_h(LAYOUT.titleMaxH)
      .overflow_hidden().child(`#${d.number} ${d.title ?? ""}`))
    .child(h_flex().gap_x(SP.md).gap_y(SP.xs).flex_wrap().items_center()
      .text_size(TEXT.caption).text_color(t.dim)
      .child(div().child(d.author ?? "")).child(div().truncate().child(d.branch ?? ""))
      .child(div().text_color(t.ansi2).child(`+${d.additions ?? 0}`))
      .child(div().text_color(t.ansi1).child(`-${d.deletions ?? 0}`))
      .child(div().child(`${d.files ?? 0} files`))
      .child(div().child(CI[d.ci] ?? ""))
      .child(div().child(d.decision ? String(d.decision).toLowerCase().replace(/_/g, " ") : "")));
}

function postPill(post) {
  const t = theme();
  return div().px(SP.sm).py(1).rounded(RADIUS.control).border_1().border_color(t.border)
    .text_size(TEXT.caption).text_color(postTone(post.tone)).child(post.label);
}

function runCard(r, { openStamp, bodyFor, onToggle }) {
  const t = theme();
  return v_flex().id(`run-${r.stamp}`).p(SP.md).gap(SP.xs).rounded(RADIUS.card).border_1()
    .border_color(t.border).cursor_pointer().hover((s) => s.bg(t.hover).border_color(t.dim))
    .on_click(() => onToggle(r.stamp))
    .child(h_flex().gap(SP.sm).items_center()
      .child(div().text_size(TEXT.body).font_medium().child(r.verdict || "review"))
      .child(div().text_size(TEXT.caption).text_color(t.dim).child(r.when))
      .child(div().text_size(TEXT.caption).text_color(t.dim).child(r.shortSha)))
    .child(div().text_size(TEXT.small).child(r.findingsText))
    .child(h_flex().gap(SP.sm).items_center().child(postPill(r.post))
      .when(Boolean(r.costText), (e) => e.child(
        div().text_size(TEXT.caption).text_color(t.dim).child(r.costText))))
    .when(openStamp === r.stamp, (e) => e.child(reviewInset(r.stamp, bodyFor(r.stamp))));
}

export function renderDetail(d, { onClose, onOpenUrl, nowMs, openStamp, bodyFor, onToggle, actionBar, outputPanel }) {
  const t = theme();
  const rows = historyRows(d.runs ?? [], nowMs);
  return v_flex().id("prs-detail").size_full().min_h(0).bg(t.background)
    .text_color(t.foreground).text_size(TEXT.body)
    .child(renderDetailHeader(d, { onClose, onOpenUrl }))
    .child(actionBar)
    .when(Boolean(outputPanel), (e) => e.child(outputPanel))
    .child(eyebrow("Review history", null, SP.lg))
    .child(v_flex().flex_1().min_h(0).overflow_y_scrollbar().px(SP.lg).pb(SP.lg).gap(SP.sm)
      .when(!rows.length, (e) => e.child(div().text_color(t.dim).child("No reviews yet")))
      .children(rows.map((r) => runCard(r, { openStamp, bodyFor, onToggle }))));
}
