// Starts `pr batch run` from Mine: form state, per-repo hc terminals, cancel/kill.
import { exec } from "herdr";
import { groupByRepo, runArgv, filterForm, launchOutcome, presetForm } from "./_lib/batch_launch.js";
import { filterOn } from "./_lib/columns.js";
import { hcBatchArgv, parseRunReply, prKey } from "./_lib/launch.js";
import { getArgv, terminalsOf } from "./_lib/busy.js";

// How often a launched terminal is looked at until its run starts or the terminal ends, and how
// much of its output is read: run_started follows one item_queued line per PR, so it stays well
// inside this many lines.
const WATCH_MS = 3000;
const READ_LINES = "400";
// Consecutive looks at a terminal hc could not answer for (about a minute) before giving up on it.
const MAX_UNREADABLE = 20;

function termKey(on, repo) {
  return `${on ?? ""}\0${repo}`;
}

function runKey(on, id) {
  return `${on ?? ""}\0${id}`;
}

export class BatchLauncher {
  // filters: { get() -> the shared prs.filters, toggle(chip name) }; the board owns the setting.
  constructor(acx, workflows, filters = { get: () => ({}), toggle: () => {} }) {
    this.acx = acx;
    this.workflows = workflows;
    this.filters = filters;
    this.selected = new Set();
    this.steps = new Set(["rebase", "ci", "comments"]);
    this.withAi = new Set();
    this.autoPublish = false;
    this.rebaseBehind = false;
    this.launching = false;
    this.note = null;
    this.terminals = new Map();
    // A launched terminal whose run has not said it started yet, per machine+repo. Its PRs stay
    // selected until it does, and come back with the reason when the terminal ends first.
    this.pending = new Map();
    this.confirmKill = null;
  }

  toggle(key) {
    if (this.selected.has(key)) this.selected.delete(key);
    else this.selected.add(key);
    this.acx.notify();
  }

  toggleRepo(keys) {
    const all = keys.length > 0 && keys.every((k) => this.selected.has(k));
    for (const k of keys) {
      if (all) this.selected.delete(k);
      else this.selected.add(k);
    }
    this.acx.notify();
  }

  toggleStep(s) {
    if (this.steps.has(s)) this.steps.delete(s);
    else this.steps.add(s);
    this.acx.notify();
  }

  toggleAi(s) {
    if (this.withAi.has(s)) this.withAi.delete(s);
    else this.withAi.add(s);
    this.acx.notify();
  }

  toggleRebaseBehind() {
    this.rebaseBehind = !this.rebaseBehind;
    this.acx.notify();
  }

  toggleAutoPublish() {
    this.autoPublish = !this.autoPublish;
    this.acx.notify();
  }

  // A Mine column's shortcut: select exactly `keys` and turn on only `preset`'s steps, with AI
  // for those steps as the preset says and every other step's AI left as it was. Starts nothing.
  applyPreset(keys, preset) {
    const form = presetForm({ keys, preset, withAi: [...this.withAi], pending: (k) => this.isPending(k) });
    this.selected = new Set(form.selected);
    this.steps = new Set(form.steps);
    this.withAi = new Set(form.withAi);
    this.rebaseBehind = form.rebaseBehind;
    this.note = null;
    this.acx.notify();
  }

  shown(prs) {
    const f = this.filters.get();
    return filterForm(prs, { repos: f.repos, labels: f.labels, drafts: filterOn(f, "mineDrafts") });
  }

  // Launching, or waiting on a launched terminal to start its run.
  get starting() {
    return this.launching || this.pending.size > 0;
  }

  isPending(key) {
    return [...this.pending.values()].some((p) => p.keys.includes(key));
  }

  addNote(message) {
    this.note = this.note ? `${this.note}; ${message}` : message;
  }

  reset() {
    for (const k of [...this.selected]) if (!this.isPending(k)) this.selected.delete(k);
    if (!this.starting) this.note = null;
    this.acx.notify();
  }

  launch(prs) {
    if (this.starting) return;
    this.launching = true;
    this.note = null;
    this.acx.notify();
    this.acx.spawn(async (cx) => {
      const errors = [];
      try {
        const selectedPrs = this.shown(prs).filter((p) => {
          const key = prKey(p.repoSlug, p.number);
          return this.selected.has(key) && !this.isPending(key);
        });
        const { groups } = groupByRepo(selectedPrs);
        for (const g of groups) {
          try {
            const argv = ["pr", ...runArgv({
              checkout: g.cwd,
              keys: g.keys,
              steps: [...this.steps],
              withAi: [...this.withAi],
              autoPublish: this.autoPublish,
              rebaseBehind: this.rebaseBehind,
            })];
            const res = parseRunReply(await exec("hc", hcBatchArgv({
              repo: g.repo, cwd: g.cwd, on: g.on, argv,
            }), {}));
            if (res.ok) {
              const tk = termKey(g.on, g.repo);
              this.terminals.set(tk, res.terminal);
              this.pending.set(tk, { terminal: res.terminal, repo: g.repo, keys: g.keys });
              this.watchLaunch(cx, tk);
            } else errors.push(`${g.repo}: ${res.error?.message ?? "hc failed"}`);
          } catch (err) {
            errors.push(String(err?.message ?? err));
          }
        }
      } finally {
        this.launching = false;
      }
      if (errors.length) this.addNote(errors.join("; "));
      cx.notify();
    });
  }

  // A terminal starting is not the batch starting: `pr batch run` can still fail on GitHub or on
  // another run holding the repo. Its PRs leave the selection only once the run says it started.
  watchLaunch(cx, tk) {
    cx.timer.after(WATCH_MS, async (cx) => {
      const p = this.pending.get(tk);
      if (!p) return;
      // Only an answer from hc decides: a failed read or lookup is looked at again rather than
      // read as an ended terminal, which would hand the PRs back while their run may be starting.
      let output = null, state = null;
      try {
        const read = await exec("hc", ["read", p.terminal, "--lines", READ_LINES], {});
        if (read.code === 0) output = read.stdout;
        const got = await exec("hc", getArgv(p.terminal), {});
        if (got.code === 0) state = terminalsOf([JSON.parse(got.stdout)])[0]?.state ?? null;
      } catch { output = state = null; }
      if (output == null || state == null) {
        p.unreadable = (p.unreadable ?? 0) + 1;
        if (p.unreadable < MAX_UNREADABLE) { this.watchLaunch(cx, tk); return; }
        this.pending.delete(tk);
        this.addNote(`${p.repo}: could not read its terminal ${p.terminal}; check it before starting again`);
        cx.notify();
        return;
      }
      p.unreadable = 0;
      const outcome = launchOutcome(output);
      if (!outcome.started && state === "live") { this.watchLaunch(cx, tk); return; }
      this.pending.delete(tk);
      if (outcome.started) {
        for (const key of p.keys) this.selected.delete(key);
        this.workflows.refresh(this.acx);
      } else {
        this.addNote(`${p.repo}: ${outcome.error || "the batch ended before it started"}`);
      }
      cx.notify();
    });
  }

  async cancel(on, id, kill) {
    const key = runKey(on, id);
    if (kill) {
      if (this.confirmKill !== key) {
        this.confirmKill = key;
        this.acx.notify();
        return;
      }
      this.confirmKill = null;
    } else {
      this.confirmKill = null;
    }
    const result = await this.workflows.cancel(on, id, { kill });
    if (!result.ok) this.note = result.message;
    this.acx.notify();
  }

  terminalFor(on, repo) {
    return this.terminals.get(termKey(on, repo)) ?? null;
  }
}
