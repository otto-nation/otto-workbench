// Models: a popover with one row per phase and an inline searchable list.
// Nothing is saved; the values live for the session and ride on the run as
// `pr --phase-model` (see _lib/model_overrides.js). `pr` validates them.
// Choices come from `pr models --json`; an older pr, a timeout or bad JSON
// fail open to free text.
import { div } from "gpui-kit";
import { h_flex, v_flex, Popover, Input, InputState } from "gpui-base";
import { theme } from "herdr";
import { invoke, readJson } from "./_lib/board_data.js";
import { overrideSummary } from "./_lib/model_overrides.js";
import { choicesArgv, parseChoices, phaseOptions, defaultLabel } from "./_lib/model_choices.js";
import { SP, RADIUS, CONTROL, LAYOUT } from "./_lib/tokens.js";
import { ghostButton, popoverSurface, text, caption, hoverRow, sectionLabel } from "./_ui/ui.js";

// Below herdr's 30 s exec cap so a hung `pr models` still settles.
const CHOICES_TIMEOUT_MS = 25_000;

const choicesByOn = new Map();

export async function fetchChoices(exec, ctx) {
  const key = String(ctx?.on ?? "");
  if (choicesByOn.has(key)) return choicesByOn.get(key);
  const pending = (async () => {
    const argv = choicesArgv(ctx?.cwd);
    const [cmd, ...args] = argv;
    const ran = await invoke(exec, ctx ?? {}, cmd, args, { timeout: CHOICES_TIMEOUT_MS });
    if (!ran.ok) return { ok: false, error: ran.error };
    const parsed = readJson(ran.stdout);
    if (!parsed.ok) return { ok: false, error: parsed.error };
    return parseChoices(parsed.value);
  })();
  choicesByOn.set(key, pending);
  return pending;
}

function failCaption(error) {
  const kind = error?.kind;
  const msg = error?.message ?? "";
  if (kind === "timeout") return "Timed out listing models — type a model id.";
  if (kind === "outdated" || /invalid choice: ['"]models['"]/i.test(msg)) {
    return "Update pr on this machine to pick from the catalogue (otto-workbench sync).";
  }
  return "Could not list models — type a model id.";
}

export class ModelPicker {
  constructor(acx, phases, id, loadChoices) {
    this.acx = acx;
    this.phases = phases;
    this.id = id;
    this.loadChoices = loadChoices;
    this.open = false;
    this.expanded = null;
    this.custom = false;
    this.query = "";
    this.loading = false;
    this.result = null;
    this.search = InputState.new({ placeholder: "Search" });
    this.search.on("change", () => {
      this.query = this.search.value();
      acx.notify();
    });
    this.fields = new Map(phases.map((phase) => {
      const state = InputState.new({ placeholder: "tier default" });
      state.on("change", () => acx.notify());
      return [phase, state];
    }));
  }

  values() {
    return Object.fromEntries(this.phases.map((p) => [p, this.fields.get(p).value()]));
  }

  summary() {
    return overrideSummary(this.values(), this.phases);
  }

  clear() {
    for (const state of this.fields.values()) state.set_value("");
    this.expanded = null;
    this.custom = false;
    this.acx.notify();
  }

  choiceLabel(phase) {
    const v = String(this.fields.get(phase).value() ?? "").trim();
    if (v) return v;
    if (this.result?.ok) return defaultLabel(this.result.doc, phase);
    return "Configured";
  }

  isCustom(phase) {
    const v = String(this.fields.get(phase).value() ?? "").trim();
    if (!v || !this.result?.ok) return false;
    const opts = phaseOptions(this.result.doc, phase, "", v);
    return !opts.sections.some((s) => s.rows.some((r) => r.ref === v));
  }

  toggle(phase) {
    if (this.expanded === phase) {
      this.expanded = null;
      this.custom = false;
    } else {
      this.expanded = phase;
      this.custom = this.isCustom(phase);
      this.query = "";
      this.search.set_value("");
    }
    this.acx.notify();
  }

  pick(phase, ref) {
    this.fields.get(phase).set_value(ref);
    this.expanded = null;
    this.custom = false;
    this.acx.notify();
  }

  ensureChoices() {
    if (this.result != null || this.loading || !this.loadChoices) return;
    this.loading = true;
    const load = this.loadChoices;
    this.acx.spawn(async (cx) => {
      let result;
      try {
        result = await load();
      } catch (err) {
        result = { ok: false, error: { kind: "exec", message: String(err?.message ?? err) } };
      }
      this.result = result?.ok ? { ok: true, doc: result.doc } : { ok: false, error: result?.error };
      this.loading = false;
      cx.notify();
    });
  }

  // The trigger plus, beside it, what is set: a button label is the action and nothing else.
  render() {
    const t = theme();
    const trigger = h_flex().flex_none().h(CONTROL.h).px(CONTROL.px).gap(SP.xs).items_center()
      .rounded(RADIUS.control).border_1().border_color(t.border)
      .hover((s) => s.bg(t.hover))
      .child(text("label", "Models"))
      .child(text("label", "▾", t.muted));
    const popover = Popover.new(`${this.id}-popover`)
      .open(this.open)
      .on_open_change((open) => {
        this.open = open;
        if (open) this.ensureChoices();
        this.acx.notify();
      })
      .trigger(trigger)
      .content(this.content());
    const summary = this.summary();
    return h_flex().flex_none().gap(SP.sm).items_center()
      .child(popover)
      .when(Boolean(summary), (e) => e.child(caption(summary)));
  }

  content() {
    if (this.result?.ok === false) return this.failOpenContent();
    if (this.result?.ok === true) return this.listContent();
    return this.loadingContent();
  }

  failOpenContent() {
    const t = theme();
    return popoverSurface()
      .child(caption(failCaption(this.result.error)))
      .child(caption("Model for the next run, per phase. Blank uses the configured one. Review phases refuse light and Haiku models."))
      .children(this.phases.map((phase) => v_flex().gap(SP.xxs)
        .child(caption(phase))
        .child(Input.new(this.fields.get(phase)).h(CONTROL.h).px(SP.sm)
          .rounded(RADIUS.control).border_1().border_color(t.input).bg(t.background))))
      .child(div().flex_none().child(ghostButton(`${this.id}-clear`, "Clear", () => this.clear())));
  }

  loadingContent() {
    return popoverSurface()
      .child(caption("Loading…"))
      .children(this.phases.map((phase) => this.phaseHeader(phase, false)))
      .child(div().flex_none().child(ghostButton(`${this.id}-clear`, "Clear", () => this.clear())));
  }

  listContent() {
    return popoverSurface()
      .child(caption("Model for the next run, per phase. Blank uses the configured one."))
      .children(this.phases.map((phase) => this.phaseBlock(phase)))
      .child(div().flex_none().child(ghostButton(`${this.id}-clear`, "Clear", () => this.clear())));
  }

  phaseHeader(phase, canChange) {
    return h_flex().flex_none().gap(SP.sm).items_center()
      .child(text("label", phase).flex_1().min_w(0).truncate())
      .child(caption(this.choiceLabel(phase)))
      .when(canChange, (e) => e.child(
        ghostButton(`${this.id}-change-${phase}`, "Change", () => this.toggle(phase)),
      ));
  }

  phaseBlock(phase) {
    const open = this.expanded === phase;
    return v_flex().gap(SP.xxs)
      .child(this.phaseHeader(phase, true))
      .when(open, (e) => e.child(this.phaseList(phase)));
  }

  phaseList(phase) {
    const t = theme();
    const opts = phaseOptions(this.result.doc, phase, this.query, this.fields.get(phase).value());
    const kids = [];
    for (const section of opts.sections ?? []) {
      kids.push(sectionLabel(section.label));
      for (const [i, row] of section.rows.entries()) kids.push(this.optionRow(phase, row, i));
    }
    return v_flex().gap(SP.xxs)
      .child(Input.new(this.search).h(CONTROL.h).px(SP.sm).rounded(RADIUS.control)
        .border_1().border_color(t.input).bg(t.background))
      .child(v_flex().id(`${this.id}-${phase}-list`).max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar()
        .children(kids))
      .child(this.customRow(phase));
  }

  optionRow(phase, row, i) {
    const t = theme();
    const id = `${this.id}-${phase}-${i}`;
    if (row.disabled) {
      return v_flex().id(id).px(SP.xs).py(SP.xxs).gap(SP.xxs)
        .child(text("label", row.label, t.muted))
        .when(Boolean(row.reason), (e) => e.child(caption(row.reason)));
    }
    return hoverRow(id, { selected: row.selected })
      .on_click(() => this.pick(phase, row.ref))
      .child(text("label", row.label).px(SP.xs).py(SP.xxs));
  }

  customRow(phase) {
    const t = theme();
    const selected = this.custom || this.isCustom(phase);
    const row = hoverRow(`${this.id}-${phase}-custom`, { selected })
      .on_click(() => {
        this.custom = true;
        this.acx.notify();
      })
      .child(text("label", "Custom…").px(SP.xs).py(SP.xxs));
    if (!this.custom && !this.isCustom(phase)) return row;
    return v_flex().gap(SP.xxs)
      .child(row)
      .child(Input.new(this.fields.get(phase)).h(CONTROL.h).px(SP.sm)
        .rounded(RADIUS.control).border_1().border_color(t.input).bg(t.background));
  }
}
