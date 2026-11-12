// Batch-run parts for a PR card and a PR's Batch tab.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import {
  STEP_ORDER, durationText, laneOf, shortKey, runStatus, stepStatus, words,
} from "./_lib/batch.js";
import { relativeTime } from "./_lib/history.js";
import { itemStatus } from "./_lib/pr_status.js";
import { SP, RADIUS, LAYOUT } from "./_lib/tokens.js";
import {
  hoverRow, card, inset, scrollBody, type, text, caption, placeholder, errorLine,
  badge, selectableCode,
} from "./_ui/ui.js";

function prCount(n) {
  return n === 1 ? "1 PR" : `${n} PRs`;
}

function cardShell(id) {
  const t = theme();
  return v_flex().id(id).p(SP.md).rounded(RADIUS.card).border_1().border_color(t.border);
}

// Recent runs. A row expands the run it names (and pins it, so cards and Batch tabs read it);
// `expanded(run)` renders what goes under the open row.
export function renderRunPanel(state, expanded) {
  const t = theme();
  const { runs, runId, pinnedOn, error, nowMs, onPickRun } = state;
  if (error) {
    return errorLine(error.message ?? String(error));
  }
  if (!runs.length) {
    return placeholder("No batch runs yet");
  }
  const isOpen = (r) => runId != null && r.id === runId && String(r.on ?? "") === String(pinnedOn ?? "");
  return v_flex().gap(SP.xs)
    .children(runs.flatMap((r, i) => {
      const open = isOpen(r);
      const row = hoverRow(`run-${r.on ?? ""}-${r.id}`, { selected: open }).flex().items_center()
        .px(SP.sm).py(SP.xs).gap(SP.md)
        .on_click(() => onPickRun(r))
        .child(text("meta", open ? "▾" : "▸", t.muted))
        .child(text("meta", i === 0 ? "Latest" : relativeTime(r.startedAt ?? "", nowMs)))
        .child(text("meta", `${prCount(r.items)} · ${(r.repos ?? []).join(", ")}`, t.muted))
        .when(Boolean(r.error), (e) => e.child(errorLine(r.error)))
        .child(div().flex_1())
        .child(badge(runStatus(r.status)));
      return open && expanded ? [row, expanded(r)] : [row];
    }));
}

// A run's PRs, one row each with its batch state. `prOf(item)` finds the board's PR for an item;
// a row with one opens it.
export function renderRunItems(doc, { repos = [], prOf, onOpen }) {
  const t = theme();
  if (doc == null) {
    return placeholder("Loading…").pl(SP.xl).py(SP.xs);
  }
  if (!doc.ok) {
    return errorLine(doc.error?.message ?? "Could not read this run").pl(SP.xl).py(SP.xs);
  }
  return v_flex().pl(SP.xl).gap(SP.xxs)
    .children((doc.items ?? []).map((item) => {
      const pr = prOf(item);
      const s = itemStatus(item);
      const row = h_flex().px(SP.sm).py(SP.xxs).gap(SP.md).items_center().min_w(0)
        .child(type(div().flex_none(), "code").text_color(t.muted).child(shortKey(item.key, repos)))
        .child(type(div().flex_1().min_w(0).truncate(), "body")
          .text_color(pr ? t.foreground : t.muted).child(pr?.title ?? item.branch ?? ""))
        .child(badge(s));
      if (!pr) return row;
      return hoverRow(`run-item-${item.key}`).on_click(() => onOpen(pr)).child(row);
    }));
}

// The steps this run was asked for, in pipeline order: a step it never ran gets no column.
export function runSteps(doc) {
  const asked = doc?.run?.steps ?? [];
  const steps = STEP_ORDER.filter((s) => asked.includes(s));
  return steps.length ? steps : STEP_ORDER;
}

function renderNotes(item) {
  const t = theme();
  const findings = item.findings ?? [];
  const wait = item.waitReason;
  const moved = item.headMoved;
  if (!findings.length && !wait && !moved) return null;
  const title = laneOf(item) === "attention" ? "Needs you" : "Notes";
  return card("batch-notes").gap(SP.xs)
    .child(text("title", title))
    .children(findings.map((f, i) => {
      const files = (f.files ?? []).join(", ");
      return v_flex().id(`finding-${i}`).gap(SP.xxs)
        .child(text("body", words(f.reason)))
        .when(Boolean(f.detail), (e) => e.child(text("body", f.detail, t.muted)))
        .when(Boolean(files), (e) => e.child(type(div(), "code").text_color(t.muted).child(files)));
    }))
    .when(Boolean(wait), (e) => e.child(text("body", wait, t.ansi3)))
    .when(moved, (e) => e.child(text("body", "Head moved since the run", t.ansi3)));
}

function renderLog(state, id) {
  const log = state.log;
  if (log == null) {
    return placeholder("Loading log…");
  }
  if (log.error) {
    return errorLine(log.error);
  }
  const lines = String(log.text ?? "").split("\n");
  return type(inset({ scroll: false }).text_color(theme().foreground), "code")
    .child(selectableCode(`step-log-${id}`, lines, LAYOUT.bodyMaxH));
}

function renderStepCard(item, name, state) {
  const t = theme();
  const rec = item.steps?.[name] ?? null;
  if (rec == null) return null;
  const dur = durationText(rec.startedAt, rec.endedAt, state.nowMs);
  const head = h_flex().gap(SP.sm).items_center()
    .child(text("title", name))
    .child(badge(stepStatus(rec.status)))
    .when(Boolean(dur), (e) => e.child(caption(dur)))
    .when(rec.exitCode != null, (e) => e.child(
      caption(`exit ${rec.exitCode}`, rec.exitCode !== 0 ? t.ansi1 : t.muted)));
  if (!rec.logPath) {
    return cardShell(`step-${name}`).gap(SP.xs)
      .child(head)
      .child(placeholder("No log"));
  }
  return card(`step-${name}`).gap(SP.xs)
    .on_click(() => state.onStep(name))
    .child(head)
    .when(state.step === name, (e) => e.child(renderLog(state, `${item.key ?? ""}-${name}`)));
}

export function renderBatchTab(item, state) {
  const body = scrollBody().px(SP.lg).pb(SP.lg).pt(SP.sm).gap(SP.sm);
  if (item == null) {
    return body.child(placeholder("This PR was not in the selected batch run"));
  }
  const notes = renderNotes(item);
  return body
    .when(Boolean(notes), (e) => e.child(notes))
    .children(runSteps(state.doc).flatMap((name) => {
      const el = renderStepCard(item, name, state);
      return el == null ? [] : [el];
    }));
}
