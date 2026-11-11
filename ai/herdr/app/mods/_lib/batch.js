/**
 * `pr batch status --full --offline` JSON (`cli.pr_batch_views.cmd_status`).
 *
 * Document: {schema_version, active, run_dir, run}. Items live at run.items.
 * Identity is item.key ("owner/name#N"); state is item.status (ItemStatus).
 * item.pr in this document is the int; the non-full report puts the key string
 * on item.pr. busyPrs reads run.items and key, or a string pr.
 * No run: CLI prints "pr batch: no batch runs yet" (not JSON).
 */

// ItemStatus values a batch is still working a PR in (ai/lib/batch/model.py).
export const BUSY_STATES = ['queued', 'waiting_admission', 'running'];

export function busyPrs(status, repo) {
  const out = new Set();
  const items = status?.run?.items;
  if (!Array.isArray(items)) return out;
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    if (!BUSY_STATES.includes(item.status)) continue;
    const ident = typeof item.key === 'string' ? item.key
      : typeof item.pr === 'string' ? item.pr
      : null;
    if (ident == null) continue;
    const i = ident.lastIndexOf('#');
    if (i < 1) continue;
    if (ident.slice(0, i) !== repo) continue;
    const n = Number(ident.slice(i + 1));
    if (!Number.isInteger(n)) continue;
    out.add(n);
  }
  return out;
}

export const STEP_ORDER = ['rebase', 'ci', 'comments', 'review'];

// Underscores as spaces: a status or finding reason as words.
export const words = (s) => String(s ?? '').replace(/_/g, ' ');
// The Start form's label per step (STEP_ORDER keys).
export const STEP_LABEL = Object.freeze({ rebase: 'Rebase conflicting PRs', ci: 'Fix CI', comments: 'Answer comments', review: 'Self-review' });
// Steps --with-ai accepts: every step but review (pr batch refuses review there).
export const AI_STEPS = Object.freeze(STEP_ORDER.filter((s) => s !== 'review'));
// batch.model.RunStatus / StepStatus as status kinds and labels, in enum order (parity test).
export const RUN_STATUS = Object.freeze({ running: ['running','running'], waiting: ['running','waiting'], done: ['pass','finished'], cancelled: ['fail','cancelled'], interrupted: ['fail','interrupted'] });
export const STEP_STATUS = Object.freeze({ pending: ['none','pending'], running: ['running','running'], done: ['pass','done'], skipped: ['none','skipped'], needs_decision: ['warn','needs decision'], failed: ['fail','failed'], interrupted: ['warn','interrupted'] });
const badgeOf = (table, v) => { const e = table[v]; return { kind: 'status', status: e ? e[0] : 'none', text: e ? e[1] : words(v) }; };
export const runStatus = (s) => badgeOf(RUN_STATUS, s);
export const stepStatus = (s) => badgeOf(STEP_STATUS, s);

export function listArgv() {
  return ['--schema-version', '2', 'batch', 'status', '--list'];
}

export function statusArgv(runId) {
  return ['--schema-version', '2', 'batch', 'status', ...(runId ? [runId] : []), '--full', '--offline'];
}

export function logTailArgv(path, n = 200) {
  return ['tail', '-n', String(n), path];
}

function isObj(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

function badJson(message) {
  return { ok: false, error: { kind: 'bad-json', message } };
}

export function parseRunList(doc) {
  if (!isObj(doc) || !Array.isArray(doc.runs)) {
    return badJson('expected object with runs array');
  }
  const runs = [];
  for (const raw of doc.runs) {
    if (!isObj(raw) || typeof raw.id !== 'string') continue;
    runs.push({
      id: raw.id,
      runDir: raw.run_dir ?? null,
      startedAt: raw.started_at ?? null,
      status: raw.status ?? null,
      active: Boolean(raw.active),
      items: typeof raw.items === 'number' ? raw.items : 0,
      repos: Array.isArray(raw.repos) ? raw.repos : [],
      error: typeof raw.error === 'string' ? raw.error : null,
    });
  }
  return { ok: true, runs };
}

function stepRec(s) {
  return {
    status: s.status ?? null,
    exitCode: s.exit_code ?? null,
    logPath: s.log_path ?? null,
    startedAt: s.started_at ?? null,
    endedAt: s.ended_at ?? null,
  };
}

function itemSteps(arr) {
  const out = Object.fromEntries(STEP_ORDER.map((n) => [n, null]));
  if (!Array.isArray(arr)) return out;
  for (const s of arr) {
    if (isObj(s) && typeof s.step === 'string') out[s.step] = stepRec(s);
  }
  return out;
}

function findingRec(f) {
  return {
    reason: f.reason ?? null,
    step: f.step ?? null,
    detail: f.detail ?? null,
    files: Array.isArray(f.files) ? f.files : [],
    logPath: f.log_path ?? null,
  };
}

function parseItem(raw) {
  return {
    key: raw.key ?? null,
    repo: raw.repo ?? null,
    pr: raw.pr ?? null,
    branch: raw.branch ?? null,
    status: raw.status ?? null,
    worktree: raw.worktree ?? null,
    waitReason: raw.wait_reason ?? null,
    headMoved: Boolean(raw.head_moved),
    steps: itemSteps(raw.steps),
    findings: Array.isArray(raw.findings) ? raw.findings.filter(isObj).map(findingRec) : [],
  };
}

export function parseRunDoc(doc) {
  if (!isObj(doc) || !isObj(doc.run)) {
    return badJson('expected object with run');
  }
  const raw = doc.run;
  return {
    ok: true,
    run: {
      id: raw.id ?? null,
      status: raw.status ?? null,
      active: Boolean(doc.active),
      runDir: doc.run_dir ?? null,
      trailRoot: raw.trail_root ?? null,
      steps: Array.isArray(raw.steps) ? raw.steps : STEP_ORDER,
      startedAt: raw.started_at ?? null,
      schemaVersion: doc.schema_version ?? null,
    },
    items: Array.isArray(raw.items) ? raw.items.filter(isObj).map(parseItem) : [],
  };
}

const ITEM_ATTENTION = new Set(['awaiting_decision', 'needs_session', 'ready_to_publish']);

export function stateChanged(paths, runDir) {
  const dir = String(runDir ?? '').replace(/\/+$/, '');
  if (dir === '') return false;
  const state = `${dir}/state.json`;
  for (const p of paths ?? []) {
    const n = String(p).replace(/\/+$/, '');
    if (n === dir || n === state) return true;
  }
  return false;
}

export function laneOf(item) {
  const status = item?.status;
  if (status === 'running') return 'running';
  if (status === 'queued' || status === 'waiting_admission') return 'waiting';
  if (ITEM_ATTENTION.has(status)) return 'attention';
  if (status === 'done') {
    return Array.isArray(item.findings) && item.findings.length > 0 ? 'attention' : 'done';
  }
  return 'closed';
}

export function shortKey(key, repos) {
  if (key == null) return '';
  if (Array.isArray(repos) && repos.length === 1 && key.startsWith(`${repos[0]}#`)) {
    return `#${key.slice(repos[0].length + 1)}`;
  }
  return key;
}

export function cardState(item) {
  if (item == null || laneOf(item) === 'closed') return null;
  for (const step of STEP_ORDER) {
    if (item.steps?.[step]?.status === 'failed') {
      return { text: `${step} failed`, tone: 'failed' };
    }
  }
  if (laneOf(item) === 'attention') return { text: 'needs you', tone: 'attention' };
  if (item.status === 'running') {
    const running = STEP_ORDER.find((s) => item.steps?.[s]?.status === 'running');
    return { text: running ? `running ${running}` : 'running', tone: 'busy' };
  }
  if (laneOf(item) === 'waiting') return { text: 'queued', tone: 'busy' };
  if (item.status === 'done') return { text: 'done', tone: 'done' };
  return null;
}

export function durationText(startedAt, endedAt, nowMs) {
  if (!startedAt) return '';
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return '';
  let end;
  if (endedAt == null || endedAt === '') {
    end = nowMs;
  } else {
    end = Date.parse(endedAt);
    if (Number.isNaN(end)) return '';
  }
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
