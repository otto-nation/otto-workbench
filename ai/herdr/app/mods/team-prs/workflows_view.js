// Batch-run parts for a PR card and a PR's Batch tab.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import {
  STEP_ORDER, durationText, laneOf, cardState, shortKey,
} from "./_lib/batch.js";
import { relativeTime } from "./_lib/history.js";
import { SP, TEXT, RADIUS } from "./_lib/tokens.js";
import {
  hoverRow, card, inset, scrollBody, mono, button,
} from "./_ui/ui.js";

function runColor(t, status) {
  if (status === "done") return t.done;
  if (status === "running" || status === "waiting") return t.working;
  if (status === "cancelled" || status === "interrupted") return t.ansi1;
  return t.muted;
}

function stepColor(t, status) {
  if (status === "failed") return t.ansi1;
  if (status === "needs_decision" || status === "interrupted") return t.ansi3;
  if (status === "running") return t.working;
  if (status === "done") return t.done;
  return t.muted;
}

function stateColor(t, tone) {
  if (tone === "failed") return t.ansi1;
  if (tone === "attention") return t.ansi3;
  if (tone === "busy") return t.working;
  if (tone === "done") return t.done;
  return t.muted;
}

const RUN_WORD = { running: "running", waiting: "waiting", done: "finished", cancelled: "cancelled", interrupted: "interrupted" };

function runWord(status) {
  return RUN_WORD[status] ?? pretty(status);
}

function prCount(n) {
  return n === 1 ? "1 PR" : `${n} PRs`;
}

function pretty(s) {
  return String(s ?? "").replace(/_/g, " ");
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
    return div().text_size(TEXT.small).text_color(t.muted).child(error.message ?? String(error));
  }
  if (!runs.length) {
    return div().text_size(TEXT.small).text_color(t.muted).child("No batch runs yet");
  }
  const isOpen = (r) => runId != null && r.id === runId && String(r.on ?? "") === String(pinnedOn ?? "");
  return v_flex().gap(SP.xs)
    .children(runs.flatMap((r, i) => {
      const open = isOpen(r);
      const row = hoverRow(`run-${r.on ?? ""}-${r.id}`).flex().items_center()
        .px(SP.sm).py(SP.xs).rounded(RADIUS.control).gap(SP.md)
        .when(open, (e) => e.bg(t.selected))
        .on_click(() => onPickRun(r))
        .child(div().text_size(TEXT.small).text_color(t.muted).child(open ? "▾" : "▸"))
        .child(div().text_size(TEXT.small).text_color(t.foreground).child(i === 0 ? "Latest" : relativeTime(r.startedAt ?? "", nowMs)))
        .child(div().text_size(TEXT.small).text_color(t.muted).child(`${prCount(r.items)} · ${(r.repos ?? []).join(", ")}`))
        .when(Boolean(r.error), (e) => e.child(div().text_size(TEXT.small).text_color(t.ansi1).child(r.error)))
        .child(div().flex_1())
        .child(div().text_size(TEXT.small).text_color(runColor(t, r.status)).child(runWord(r.status)));
      return open && expanded ? [row, expanded(r)] : [row];
    }));
}

// A run's PRs, one row each with its batch state. `prOf(item)` finds the board's PR for an item;
// a row with one opens it.
export function renderRunItems(doc, { repos = [], prOf, onOpen }) {
  const t = theme();
  if (doc == null) {
    return div().pl(SP.xl).py(SP.xs).text_size(TEXT.small).text_color(t.muted).child("Loading…");
  }
  if (!doc.ok) {
    return div().pl(SP.xl).py(SP.xs).text_size(TEXT.small).text_color(t.muted).child(doc.error?.message ?? "Could not read this run");
  }
  return v_flex().pl(SP.xl).gap(SP.xxs)
    .children((doc.items ?? []).map((item) => {
      const pr = prOf(item);
      const state = cardState(item);
      const row = h_flex().px(SP.sm).py(SP.xxs).gap(SP.md).items_center().min_w(0)
        .child(div().flex_none().text_size(TEXT.small).text_color(t.muted).child(shortKey(item.key, repos)))
        .child(div().flex_1().min_w(0).truncate().text_size(TEXT.small)
          .text_color(pr ? t.foreground : t.muted).child(pr?.title ?? item.branch ?? ""))
        .child(div().flex_none().text_size(TEXT.small)
          .text_color(state ? stateColor(t, state.tone) : t.muted).child(state?.text ?? String(item.status ?? "")));
      if (!pr) return row;
      return hoverRow(`run-item-${item.key}`).rounded(RADIUS.control).on_click(() => onOpen(pr)).child(row);
    }));
}

export function cardStateLine(item) {
  const t = theme();
  const state = cardState(item);
  if (state == null) return null;
  return h_flex().gap(SP.xxs).text_size(TEXT.caption)
    .child(div().text_color(t.muted).child("Batch · "))
    .child(div().text_color(stateColor(t, state.tone)).child(state.text));
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
    .child(div().text_size(TEXT.title).font_semibold().child(title))
    .children(findings.map((f, i) => {
      const files = (f.files ?? []).join(", ");
      return v_flex().id(`finding-${i}`).gap(SP.xxs)
        .child(div().text_size(TEXT.small).text_color(t.foreground).child(pretty(f.reason)))
        .when(Boolean(f.detail), (e) => e.child(
          div().text_size(TEXT.small).text_color(t.muted).child(f.detail)))
        .when(Boolean(files), (e) => e.child(
          div().text_size(TEXT.caption).text_color(t.muted).child(files)));
    }))
    .when(Boolean(wait), (e) => e.child(
      div().text_size(TEXT.small).text_color(t.ansi3).child(wait)))
    .when(moved, (e) => e.child(
      div().text_size(TEXT.small).text_color(t.ansi3).child("head moved since the run")));
}

function renderLog(state) {
  const t = theme();
  const log = state.log;
  if (log == null) {
    return div().text_size(TEXT.small).text_color(t.muted).child("Loading log…");
  }
  if (log.error) {
    return div().text_size(TEXT.small).text_color(t.muted).child(log.error);
  }
  const lines = String(log.text ?? "").split("\n");
  return inset().font_family(mono()).text_size(TEXT.caption).text_color(t.foreground)
    .children(lines.map((l) => div().child(l === "" ? " " : l)));
}

function renderStepCard(item, name, state) {
  const t = theme();
  const rec = item.steps?.[name] ?? null;
  if (rec == null) return null;
  const dur = durationText(rec.startedAt, rec.endedAt, state.nowMs);
  const head = h_flex().gap(SP.sm).items_center()
    .child(div().text_size(TEXT.title).font_semibold().child(name))
    .child(div().text_size(TEXT.small).text_color(stepColor(t, rec.status)).child(pretty(rec.status)))
    .when(Boolean(dur), (e) => e.child(div().text_size(TEXT.small).text_color(t.muted).child(dur)))
    .when(rec.exitCode != null, (e) => e.child(
      div().text_size(TEXT.small)
        .text_color(rec.exitCode !== 0 ? t.ansi1 : t.muted)
        .child(`exit ${rec.exitCode}`)));
  if (!rec.logPath) {
    return cardShell(`step-${name}`).gap(SP.xs)
      .child(head)
      .child(div().text_size(TEXT.small).text_color(t.muted).child("no log"));
  }
  return card(`step-${name}`).gap(SP.xs)
    .on_click(() => state.onStep(name))
    .child(head)
    .when(state.step === name, (e) => e.child(renderLog(state)));
}

export function renderBatchTab(item, state) {
  const t = theme();
  const body = scrollBody().px(SP.lg).pb(SP.lg).pt(SP.sm).gap(SP.sm);
  if (item == null) {
    return body.child(div().text_size(TEXT.small).text_color(t.muted)
      .child("This PR was not in the selected batch run"));
  }
  const notes = renderNotes(item);
  return body
    .when(Boolean(notes), (e) => e.child(notes))
    .children(runSteps(state.doc).flatMap((name) => {
      const el = renderStepCard(item, name, state);
      return el == null ? [] : [el];
    }));
}
