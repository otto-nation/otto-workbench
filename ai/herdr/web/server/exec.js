/**
 * The herdr `exec(cmd, args, {cwd, on, timeout})` contract `_lib/board_data.js` is written against,
 * on a server. Resolves {code, stdout, stderr}; rejects only when a local program cannot start or a
 * remote `hc exec` fails or times out, so board_data's classifyCaught sees what it sees under herdr.
 *
 * Local commands spawn with stdin closed, in their own process group (killed whole on timeout), with
 * output capped and at most `maxProcs` children at once. `hc` means the configured herdr-core CLI.
 * Remote commands (`on` set) run through `hc exec --json --on M`, whose stdout/stderr are base64.
 */
import { spawn } from 'node:child_process';
import { readFile, realpath, stat } from 'node:fs/promises';
import { posix } from 'node:path';

export const DEFAULT_TIMEOUT_MS = 30000;
export const MAX_OUTPUT_BYTES = 64 * 1024 * 1024;
const HC_EXEC_MAX_S = 600;
// Grace on top of a remote command's own timeout before the local `hc exec` itself is abandoned.
const REMOTE_GRACE_MS = 15000;

// A server started inside an hc terminal would parent every `hc run --bg` to it (HC_TERMINAL_ID),
// and a core the first `hc` call starts would hand the access token to every terminal (PR_BOARD_*).
export function childEnv(env) {
  const out = {};
  for (const [k, v] of Object.entries(env ?? {})) {
    if (k === 'HC_TERMINAL_ID' || k.startsWith('PR_BOARD_')) continue;
    out[k] = v;
  }
  return { ...out, GH_PROMPT_DISABLED: '1', GIT_TERMINAL_PROMPT: '0', NO_COLOR: '1' };
}

function semaphore(n) {
  let active = 0;
  const waiting = [];
  return async (fn) => {
    if (active >= n) await new Promise((resolve) => waiting.push(resolve));
    active++;
    try {
      return await fn();
    } finally {
      active--;
      waiting.shift()?.();
    }
  };
}

function runLocal(cmd, args, { cwd, timeout, env }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = (fn, value) => { if (!settled) { settled = true; fn(value); } };
    let child;
    try {
      child = spawn(cmd, args, { cwd: cwd ?? undefined, env, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    } catch (err) {
      done(reject, err);
      return;
    }
    const out = [];
    const errs = [];
    let size = 0;
    let over = false;
    let timedOut = false;
    const killGroup = () => {
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* the group is already gone */ }
    };
    const timer = setTimeout(() => { timedOut = true; killGroup(); }, timeout);
    const take = (bucket) => (chunk) => {
      size += chunk.length;
      if (size > MAX_OUTPUT_BYTES) { over = true; killGroup(); return; }
      bucket.push(chunk);
    };
    child.stdout.on('data', take(out));
    child.stderr.on('data', take(errs));
    child.on('error', (err) => { clearTimeout(timer); done(reject, err); });
    child.on('close', (code) => {
      clearTimeout(timer);
      let stderr = Buffer.concat(errs).toString('utf8');
      if (timedOut) stderr += `\n${cmd} took longer than ${Math.round(timeout / 1000)} s`;
      if (over) stderr += `\n${cmd} printed more than ${MAX_OUTPUT_BYTES} bytes`;
      const status = timedOut ? 124 : over ? 1 : (code ?? 1);
      done(resolve, { code: status, stdout: Buffer.concat(out).toString('utf8'), stderr });
    });
  });
}

const fromB64 = (s) => (typeof s === 'string' ? Buffer.from(s, 'base64').toString('utf8') : '');

async function runRemote(hcBin, cmd, args, { cwd, on, timeout, env }) {
  const secs = Math.min(HC_EXEC_MAX_S, Math.max(1, Math.ceil(timeout / 1000)));
  const hcArgs = ['exec', '--json', '--on', on, '--timeout', String(secs)];
  if (cwd) hcArgs.push('--cwd', cwd);
  hcArgs.push('--', cmd, ...args);
  const ran = await runLocal(hcBin, hcArgs, { cwd: null, timeout: secs * 1000 + REMOTE_GRACE_MS, env });
  let doc;
  try {
    doc = JSON.parse(ran.stdout);
  } catch {
    throw new Error((ran.stderr || `hc exec exit ${ran.code}`).trim());
  }
  if (doc?.timed_out) throw new Error(`took longer than ${secs} s`);
  return { code: doc?.code ?? 1, stdout: fromB64(doc?.stdout), stderr: fromB64(doc?.stderr) };
}

export function makeExec({ hcBin = 'hc', env = process.env, maxProcs = 4, defaultTimeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const gate = semaphore(maxProcs);
  const cenv = childEnv(env);
  return function exec(cmd, args, opts = {}) {
    const timeout = opts.timeout ?? defaultTimeoutMs;
    return gate(async () => {
      if (opts.on) return runRemote(hcBin, cmd, args, { cwd: opts.cwd, on: opts.on, timeout, env: cenv });
      if (opts.cwd) {
        const ok = await stat(opts.cwd).then((s) => s.isDirectory(), () => false);
        if (!ok) return { code: 1, stdout: '', stderr: `not a directory: ${opts.cwd}` };
      }
      return runLocal(cmd === 'hc' ? hcBin : cmd, args, { cwd: opts.cwd, timeout, env: cenv });
    });
  };
}

export function isInside(root, path) {
  const base = root.endsWith('/') ? root : `${root}/`;
  return path.startsWith(base);
}

/**
 * readTextFile(path, {on}) for board_data.readReview: only files inside the reviews root that
 * `pr review --list` reported for that machine (rootFor(on)). Local paths are resolved with realpath
 * first, so a symlink cannot lead out. Missing file: null.
 */
export function makeReadTextFile({ exec, rootFor }) {
  return async function readTextFile(path, { on } = {}) {
    const root = rootFor(on ?? null);
    if (!root) throw new Error('no reviews root is known for this machine yet');
    if (on) {
      const norm = posix.normalize(path);
      // ceiling: string check only for a remote path; resolve it there (hc exec realpath) if a remote
      // reviews root can ever hold symlinks that point out of it
      if (!isInside(posix.normalize(root), norm)) throw new Error('outside the reviews root');
      const ran = await exec('cat', [norm], { on });
      if (ran.code === 0) return ran.stdout;
      if (/No such file/i.test(ran.stderr)) return null;
      throw new Error((ran.stderr || `cat exit ${ran.code}`).trim());
    }
    let real;
    try {
      real = await realpath(path);
    } catch (err) {
      if (err.code === 'ENOENT') return null;
      throw err;
    }
    if (!isInside(await realpath(root), real)) throw new Error('outside the reviews root');
    return readFile(real, 'utf8');
  };
}
