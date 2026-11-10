// Starts `pr batch run` from Mine: form state, per-repo hc terminals, cancel/kill.
import { exec } from "herdr";
import { groupByRepo, runArgv, filterForm } from "./_lib/batch_launch.js";
import { hcBatchArgv, parseRunReply, prKey } from "./_lib/launch.js";

function termKey(on, repo) {
  return `${on ?? ""}\0${repo}`;
}

function runKey(on, id) {
  return `${on ?? ""}\0${id}`;
}

export class BatchLauncher {
  constructor(acx, workflows) {
    this.acx = acx;
    this.workflows = workflows;
    this.selected = new Set();
    this.repoFilter = new Set();
    this.labelFilter = new Set();
    this.steps = new Set(["rebase", "ci", "comments"]);
    this.withAi = new Set();
    this.autoPublish = false;
    this.rebaseBehind = false;
    this.launching = false;
    this.note = null;
    this.terminals = new Map();
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

  toggleRepoFilter(slug) {
    const key = [...this.repoFilter].find((r) => r.toLowerCase() === String(slug).toLowerCase());
    if (key != null) this.repoFilter.delete(key);
    else this.repoFilter.add(slug);
    this.acx.notify();
  }

  toggleLabelFilter(name) {
    const key = [...this.labelFilter].find((n) => n.toLowerCase() === String(name).toLowerCase());
    if (key != null) this.labelFilter.delete(key);
    else this.labelFilter.add(name);
    this.acx.notify();
  }

  shown(prs) {
    return filterForm(prs, { repos: [...this.repoFilter], labels: [...this.labelFilter] });
  }

  reset() {
    this.selected.clear();
    this.note = null;
    this.acx.notify();
  }

  launch(prs, onDone) {
    if (this.launching) return;
    this.launching = true;
    this.note = null;
    this.acx.notify();
    this.acx.spawn(async (cx) => {
      const errors = [];
      try {
        const selectedPrs = this.shown(prs).filter((p) => this.selected.has(prKey(p.repoSlug, p.number)));
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
              this.terminals.set(termKey(g.on, g.repo), res.terminal);
              // Launched: out of the selection, so a retry after another repo fails does not start it twice.
              for (const key of g.keys) this.selected.delete(key);
            } else errors.push(`${g.repo}: ${res.error?.message ?? "hc failed"}`);
          } catch (err) {
            errors.push(String(err?.message ?? err));
          }
        }
      } finally {
        this.launching = false;
      }
      if (errors.length) this.note = errors.join("; ");
      else {
        this.reset();
        onDone?.();
      }
      this.workflows.refresh(this.acx);
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
