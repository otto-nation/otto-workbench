import { div } from "gpui-kit";
import { h_flex, TextView } from "gpui-base";
import { theme } from "herdr";
import { historyRows } from "./_lib/history.js";
import { tailLines } from "./_lib/output.js";
import { SP, TEXT, LAYOUT } from "./_lib/tokens.js";
import { segment, segmented, button, tag, inset, ciMark, addStat, delStat, card, placeholder, caption, paneBar, scrollBody, pane, mono } from "./_ui/ui.js";

function postTone(tone) {
  const t = theme();
  if (tone === "success") return t.ansi2;
  if (tone === "warning") return t.ansi1;
  if (tone === "info") return t.accent;
  return t.dim;
}

function reviewBody(stamp, body) {
  if (body == null) return placeholder("loading…");
  if (body.ok === false) return placeholder(body.error?.message ?? "");
  if (body.text == null) return placeholder("review file not found — it may have been pruned");
  return TextView.markdown("review-" + stamp, body.text).selectable();
}

function reviewInset(stamp, body) {
  return inset().mt(SP.sm).child(reviewBody(stamp, body));
}

// Enabled actions as buttons; when none is enabled, the first reason says why. A post action
// asks first (confirm), because it writes to GitHub.
export function renderActionBar(actions, { confirm, note, onAction, onConfirm, onCancel }) {
  const t = theme();
  if (confirm) {
    const c = confirm.confirm;
    return paneBar()
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
    .when(!on.length && Boolean(why), (e) => e.child(caption(why.reason)))
    .when(Boolean(note), (e) => e.child(caption(note, t.ansi1)));
}

// What the detail view needs from a board card.
export function detailOf(pr) {
  return {
    repo: pr.repoSlug, host: pr.host, number: pr.number, title: pr.title, author: pr.author,
    branch: pr.branch, ci: pr.ciStatus, decision: pr.reviewDecision, additions: pr.additions,
    deletions: pr.deletions, files: pr.changedFiles, url: pr.url, on: pr.on ?? null,
    runs: (pr.reviewRuns ?? []).map((r) => r.run),
    cwd: pr.cwd ?? null, headMoved: Boolean(pr.hasNewChangesSinceReview),
    batchBusy: Boolean(pr.busy), live: pr.reviewTerminal ?? null, sha: pr.headSha ?? "",
  };
}

// The run's text, read-only, inside the pane. The terminal itself can open as a tab for anything
// interactive.
export function renderOutput(output, { onHide, onTab }) {
  const lines = tailLines(output.text ?? "", 80);
  return paneBar()
    .child(h_flex().gap(SP.sm).items_center().justify_between()
      .child(caption(output.live ? "Run output · live" : "Run output"))
      .child(h_flex().gap(SP.sm)
        .child(button("out-tab", "Open as tab", onTab))
        .child(button("out-hide", "Hide", onHide))))
    .child(inset().flex().flex_col().text_size(TEXT.caption).font_family(mono())
      .when(output.text == null, (e) => e.child(placeholder("loading…")))
      .when(output.text != null && !lines.length, (e) => e.child(placeholder("no output yet")))
      .children(lines.map((l) => div().child(l === "" ? " " : l))));
}

export function prUrl(d) {
  return d.url || `https://${d.host || "github.com"}/${d.repo}/pull/${d.number}`;
}

export function renderDetailHeader(d, { onClose, onOpenUrl, onReview, reviewState }) {
  const t = theme();
  return paneBar().p(SP.lg)
    .child(h_flex().gap(SP.sm).flex_wrap().items_center().justify_between()
      .child(button("detail-back", "← Back", () => onClose()))
      .child(h_flex().gap(SP.sm).flex_wrap()
        .child(button("detail-review", "Open for Review", () => onReview(), { disabled: !reviewState.enabled }))
        .child(button("detail-open", "View on GitHub", () => onOpenUrl(prUrl(d))))))
    .when(!reviewState.enabled, (e) => e.child(caption(reviewState.reason)))
    .child(div().min_w(0).text_size(TEXT.title).font_semibold().max_h(LAYOUT.titleMaxH)
      .overflow_hidden().child(`#${d.number} ${d.title ?? ""}`))
    .child(h_flex().gap_x(SP.md).gap_y(SP.xs).flex_wrap().items_center()
      .text_size(TEXT.caption).text_color(t.dim)
      .child(div().child(d.author ?? "")).child(div().truncate().child(d.branch ?? ""))
      .child(addStat(d.additions ?? 0))
      .child(delStat(d.deletions ?? 0))
      .child(div().child(`${d.files ?? 0} files`))
      .when(d.generated > 0, (e) => e.child(div().child(`excl. ${d.generated} generated`)))
      .child(ciMark(d.ci))
      .child(div().child(d.decision ? String(d.decision).toLowerCase().replace(/_/g, " ") : "")));
}

function runCard(r, { openStamp, bodyFor, onToggle }) {
  return card(`run-${r.stamp}`).gap(SP.xs)
    .on_click(() => onToggle(r.stamp))
    .child(h_flex().gap(SP.sm).items_center()
      .child(div().text_size(TEXT.body).font_medium().child(r.verdict || "review"))
      .child(caption(r.when))
      .child(caption(r.shortSha)))
    .child(div().text_size(TEXT.small).child(r.findingsText))
    .child(h_flex().gap(SP.sm).items_center().child(tag(r.post.label, postTone(r.post.tone)))
      .when(Boolean(r.costText), (e) => e.child(caption(r.costText))))
    .when(openStamp === r.stamp, (e) => e.child(reviewInset(r.stamp, bodyFor(r.stamp))));
}

export function renderDetail(d, {
  onClose, onOpenUrl, onReview, reviewState, nowMs, openStamp, bodyFor, onToggle, actionBar, outputPanel,
  tab, onTab, filesBody, batchBody,
}) {
  const rows = historyRows(d.runs ?? [], nowMs);
  const segs = [
    segment("tab-history", "Review history", tab === "history", () => onTab("history")),
    segment("tab-files", "Files", tab === "files", () => onTab("files")),
  ];
  if (batchBody != null) {
    segs.push(segment("tab-batch", "Batch", tab === "batch", () => onTab("batch")));
  }
  return pane("prs-detail")
    .text_size(TEXT.body)
    .child(renderDetailHeader(d, { onClose, onOpenUrl, onReview, reviewState }))
    .child(actionBar)
    .when(Boolean(outputPanel), (e) => e.child(outputPanel))
    .child(h_flex().flex_none().px(SP.lg).pt(SP.md).child(segmented(segs)))
    .when(tab === "files", (e) => e.child(filesBody))
    .when(tab === "batch", (e) => e.child(batchBody))
    .when(tab !== "files" && tab !== "batch", (e) => e.child(scrollBody().px(SP.lg).pb(SP.lg)
      .pt(SP.sm).gap(SP.sm)
      .when(!rows.length, (x) => x.child(placeholder("No reviews yet")))
      .children(rows.map((r) => runCard(r, { openStamp, bodyFor, onToggle })))));
}
