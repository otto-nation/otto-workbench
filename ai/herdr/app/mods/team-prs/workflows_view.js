// Batch-run parts for the board header, a PR card, and a PR's Batch tab.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme, run } from "herdr";
import {
  STEP_ORDER, durationText, laneOf, cardState,
} from "./_lib/batch.js";
import { STEP_NAMES } from "./_lib/batch_launch.js";
import { relativeTime } from "./_lib/history.js";
import { SP, TEXT, RADIUS } from "./_lib/tokens.js";
import {
  hoverRow, card, inset, scrollBody, mono, chip, button, link,
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

function sectionLabel(text) {
  const t = theme();
  return div().font_family(mono()).text_size(TEXT.caption).text_color(t.muted).child(text);
}

function cardShell(id) {
  const t = theme();
  return v_flex().id(id).p(SP.md).rounded(RADIUS.card).border_1().border_color(t.border);
}

export function batchButtonLabel(state) {
  const { runs, runId, doc, error, loading, nowMs } = state;
  if (error) return "Batch · unavailable";
  if (loading && !runs.length) return "Batch · loading…";
  if (!runs.length) return "Batch · none";
  const latest = runId == null || runId === runs[0]?.id;
  const picked = runs.find((r) => r.id === (runId ?? runs[0]?.id));
  const status = doc?.run?.status ?? picked?.status;
  const when = relativeTime(doc?.run?.startedAt ?? picked?.startedAt ?? "", nowMs);
  const word = runWord(status);
  // The run records when it started, not when it ended, so the time leads and the state follows.
  const tail = when ? `${when} · ${word}` : word;
  return latest ? `Batch · ${tail}` : `Batch · earlier run · ${tail}`;
}

export function lastBatchLabel(state) {
  return `Last batch · ${batchButtonLabel(state).replace(/^Batch · /, "")}`;
}

export function renderRunPanel(state) {
  const t = theme();
  const { on, runs, runId, error, nowMs, onPickRun } = state;
  const where = String(on ?? "this computer").toUpperCase();
  const panel = v_flex().px(SP.lg).py(SP.sm).gap(SP.xs).flex_none()
    .border_b_1().border_color(t.border)
    .child(sectionLabel(`BATCH RUNS · ${where}`));
  if (error) {
    return panel.child(div().text_size(TEXT.small).text_color(t.muted).child(error.message ?? String(error)));
  }
  if (!runs.length) {
    return panel.child(div().text_size(TEXT.small).text_color(t.muted).child("No batch runs on this machine"));
  }
  const current = runId ?? runs[0]?.id;
  return panel.children(runs.map((r, i) => hoverRow(`run-${r.id}`).flex().items_center()
    .px(SP.sm).py(SP.xs).rounded(RADIUS.control).gap(SP.md)
    .when(r.id === current, (e) => e.bg(t.selected))
    .on_click(() => onPickRun(i === 0 ? null : r.id))
    .child(div().text_size(TEXT.small).text_color(t.foreground).child(i === 0 ? "Latest" : relativeTime(r.startedAt ?? "", nowMs)))
    .child(div().text_size(TEXT.small).text_color(t.muted).child(`${prCount(r.items)} · ${(r.repos ?? []).join(", ")}`))
    .when(Boolean(r.error), (e) => e.child(div().text_size(TEXT.small).text_color(t.ansi1).child(r.error)))
    .child(div().flex_1())
    .child(div().text_size(TEXT.small).text_color(runColor(t, r.status)).child(runWord(r.status)))));
}

function shortRepo(slug) {
  return String(slug ?? "").split("/").pop();
}

export function renderSelectionBar({ launcher: L, prs }) {
  const t = theme();
  const n = L.selected.size;
  const selected = (prs ?? []).filter((p) => L.selected.has(`${p.repoSlug}#${p.number}`));
  const repos = [...new Set(selected.map((p) => shortRepo(p.repoSlug)).filter(Boolean))];
  const runLabel = L.launching ? "Starting…" : `Run on ${n} PR${n === 1 ? "" : "s"}`;
  const disabled = n === 0 || L.steps.size === 0 || L.launching;
  return v_flex().px(SP.lg).py(SP.sm).gap(SP.xs).flex_none()
    .border_b_1().border_color(t.border)
    .child(h_flex().gap(SP.md).items_center().flex_wrap()
      .child(div().text_size(TEXT.small).text_color(t.foreground).child(`${n} selected`))
      .child(link("sel-all", "All", () => L.selectAll(prs)))
      .child(link("sel-none", "None", () => L.selectNone()))
      .child(div().text_size(TEXT.small).text_color(t.muted).child("|"))
      .children(STEP_NAMES.map((s) => chip(`step-${s}`, s, L.steps.has(s), () => L.toggleStep(s))))
      .child(link("sel-options", L.options ? "Hide options" : "Options", () => L.toggleOptions()))
      .child(div().flex_1())
      .when(Boolean(L.note), (e) => e.child(
        div().text_size(TEXT.small).text_color(t.ansi1).child(L.note)))
      .child(button("batch-run", runLabel, () => L.launch(prs), { disabled }))
      .child(link("sel-cancel", "Cancel", () => L.stop())))
    .when(repos.length > 1, (e) => e.child(
      div().text_size(TEXT.small).text_color(t.muted).child(`One run per repo: ${repos.join(", ")}`)))
    .when(L.options, (e) => e.child(h_flex().gap(SP.sm).items_center().flex_wrap()
      .child(div().text_size(TEXT.small).text_color(t.muted).child("Use AI for"))
      .children(["rebase", "ci", "comments"].map((s) => chip(`ai-${s}`, s, L.withAi.has(s), () => L.toggleAi(s))))
      .child(chip("auto-publish", "Push clean fast-forwards", L.autoPublish, () => L.toggleAutoPublish()))));
}

function terminalOf(L, on, repos) {
  for (const repo of repos ?? []) {
    const id = L.terminalFor(on, repo);
    if (id) return id;
  }
  return null;
}

export function renderRunStrip({ launcher: L, workflows }) {
  const t = theme();
  const runs = workflows.activeRuns();
  if (!runs.length) return null;
  return v_flex().flex_none().children(runs.map((entry) => {
    const on = entry.on;
    const id = entry.run.id;
    const killKey = `${on ?? ""}\0${id}`;
    const confirm = L.confirmKill === killKey;
    const repos = (entry.run.repos ?? []).map(shortRepo).join(", ");
    const terminal = terminalOf(L, on, entry.run.repos);
    const progress = entry.progress ?? { done: 0, total: 0 };
    return h_flex().id(`run-strip-${killKey}`).px(SP.lg).py(SP.xs).gap(SP.md).items_center()
      .border_b_1().border_color(t.border)
      .child(div().text_size(TEXT.small).text_color(t.foreground).child("Batch"))
      .child(div().text_size(TEXT.small).text_color(t.muted).child(`${repos} · ${on ?? "this computer"}`))
      .child(div().text_size(TEXT.small).text_color(t.working).child("running"))
      .child(div().text_size(TEXT.small).text_color(t.muted).child(`${progress.done} of ${progress.total} done`))
      .child(div().flex_1())
      .when(Boolean(terminal), (e) => e.child(
        link(`log-${killKey}`, "Log", () => run("terminal.keep", { terminal }))))
      .child(link(`cancel-${killKey}`, "Cancel", () => { void L.cancel(on, id, false); }))
      .child(div().id(`kill-${killKey}`).text_size(TEXT.caption)
        .text_color(confirm ? t.ansi1 : t.muted).cursor_pointer()
        .hover((s) => s.text_color(t.foreground))
        .on_click(() => { void L.cancel(on, id, true); })
        .child(confirm ? "Confirm kill" : "Kill"));
  }));
}

export function cardCheckbox(on) {
  const t = theme();
  return div().flex_none().w(SP.md).h(SP.md).rounded(SP.xxs).border_1().border_color(t.dim)
    .when(Boolean(on), (e) => e.bg(t.foreground));
}

export function renderPrStepChips(pr, launcher) {
  const key = `${pr.repoSlug}#${pr.number}`;
  const set = launcher.perPr.get(key) ?? launcher.steps;
  return h_flex().gap(SP.xs).items_center().flex_wrap()
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
    .children(STEP_NAMES.map((s) => chip(`pr-${key}-${s}`, s, set.has(s), () => launcher.togglePrStep(key, s))));
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
