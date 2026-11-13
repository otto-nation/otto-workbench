/**
 * Run the board's commands through `exec`: the `pr board` read, files and patches,
 * terminals, tool checks.
 *
 * `exec(cmd, args, {cwd, on})` returns `Promise<{code, stdout, stderr}>`
 * and rejects on a timeout, a program that cannot start, or an unreachable
 * machine. Every exported loader takes `exec` first and `ctx = {cwd, on}`;
 * none throw. Failures are
 * `{ok:false, error:{kind, message}}` with kind `missing-tool`,
 * `timeout`, `exec`, `auth`, `failed`, or `bad-json`.
 */

import { boardArgv, PHASE_TIMEOUT_MS } from './board_doc.js';
import { busyMap, getArgv, lsArgv, terminalsOf } from './busy.js';
import {
  FILES_PER_PAGE,
  filesArgv,
  filesPageArgv,
  pagePatchesArgv,
  patchArgv,
} from './github.js';
import { attrArgvs, generatedPaths, mapFiles, markGenerated } from './diff.js';

function messageOf(err) {
  if (err == null) return '';
  if (typeof err === 'string') return err;
  if (typeof err.message === 'string') return err.message;
  return String(err);
}

// herdr prefixes its exec errors; over its output cap it throws a bare `hc exec:`.
function execReason(err) {
  return messageOf(err).replace(/^`herdr\.exec`:\s*/, '').replace(/^hc exec:\s*$/, '').trim();
}

function labelOf(cmd, args) {
  return [cmd, ...args.slice(0, 2)].join(' ');
}

function classifyCaught(err, on, command) {
  if (on == null || on === '') return { kind: 'missing-tool', message: messageOf(err) };
  const message = execReason(err);
  const kind = /took longer than/i.test(message) ? 'timeout' : 'exec';
  return { kind, command, message };
}

const OUTDATED_RE = /invalid choice: 'board'|unsupported --schema-version|--schema-version is served by .*not by this command|pr board: error: (unrecognized arguments|argument --phase: invalid choice)/;

export function classifyCode(result) {
  const stderr = result.stderr ?? '';
  // A remote machine that has not synced: a stale workbench, not a missing tool.
  if (result.code === 127 && stderr.includes(REMOTE_ENV_MISSING)) {
    return { kind: 'outdated', message: stderr.trim() };
  }
  if (result.code === 127) {
    return { kind: 'missing-tool', message: stderr || 'command not found' };
  }
  // coreutils `timeout` exits 124 when the deadline fires.
  if (result.code === 124) {
    return { kind: 'timeout', message: stderr || 'exit 124' };
  }
  if (
    stderr.includes('gh auth login') ||
    /authentication/i.test(stderr) ||
    /HTTP\s*401/i.test(stderr)
  ) {
    return { kind: 'auth', message: stderr };
  }
  // coreutils' `pr` answers the workbench's flags this way: it is first on PATH, not the workbench's.
  if (/^pr: unrecognized option/m.test(stderr)) {
    return {
      kind: 'failed',
      message: "a different `pr` is first on PATH, not the workbench's (try `otto-workbench sync`)",
    };
  }
  // An older workbench `pr` on that machine: it lacks `board`, this schema or a flag.
  if (OUTDATED_RE.test(stderr)) return { kind: 'outdated', message: stderr.trim() };
  if (result.code !== 0) {
    return { kind: 'failed', message: stderr || `exit ${result.code}` };
  }
  return null;
}

// A command exec'd on another machine is started by its herdr core with the system PATH, not by a
// login shell, so it lacks everything the shell's startup files provide: ~/.local/bin (without it
// `pr` resolves to coreutils' pagination tool of the same name), mise's shims (`wt`, go, node on a
// host without Homebrew), and ~/.env.local (GOOGLE_CLOUD_PROJECT and the like, without which pi
// never registers its Vertex provider). The workbench's zsh/env.sh is the one definition of that
// environment, deployed to a HOME-fixed path (ZSH_ENV_DST in lib/constants.sh;
// tests/workbench_env.bats holds the two together); `workbench_env` loads it and never ends this
// shell. A machine that has not synced since env.sh existed has no copy, and fails here, loudly,
// rather than running coreutils' `pr` or a half-set environment. Local calls keep the app's own PATH.
export const REMOTE_ENV_FILE = '.config/zsh/env.sh';
export const REMOTE_ENV_MISSING = 'workbench env (~/' + REMOTE_ENV_FILE + ') is missing on this machine '
  + '— run otto-workbench sync there';
const REMOTE_ENV = 'if [ -r "$HOME/' + REMOTE_ENV_FILE + '" ]; then . "$HOME/' + REMOTE_ENV_FILE + '"; workbench_env; '
  + 'else echo "' + REMOTE_ENV_MISSING + '" >&2; exit 127; fi; ';

export const REMOTE_PREAMBLE = REMOTE_ENV + 'exec "$@"';

// Isolated remote children leave herdr-core serve's 512M cgroup (MemoryMax=512M,
// MemorySwapMax=0 on the transient unit `serve` starts for exec) so a heavy
// command cannot throttle the connection, and self-terminate at the deadline.
// `$1` is seconds, `$2` is MemoryMax, `$3` is `scope` or `plain`; all are
// positional so they are not interpolated into this script. `scope` uses
// systemd-run when it and the user bus socket exist — no probe process per
// call; `plain` (and any machine without them) runs under a bare `timeout`.
export const REMOTE_ISOLATE_PREAMBLE = REMOTE_ENV
  + 'seconds=$1; memoryMax=$2; mode=$3; shift 3; '
  + 'if [ "$mode" = scope ] && command -v systemd-run >/dev/null 2>&1 '
  + '&& [ -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then '
  + 'exec systemd-run --user --scope --quiet --collect -p MemoryMax="$memoryMax" -p MemorySwapMax=0 -- timeout -k 5 "$seconds" "$@"; '
  + 'elif command -v timeout >/dev/null 2>&1; then '
  + 'exec timeout -k 5 "$seconds" "$@"; '
  + 'else exec "$@"; fi';

// systemd-run's own refusals: the scope never started, so the command never ran. pr prints none of
// these, which is what makes a retry under a bare `timeout` safe rather than a second attempt.
const SCOPE_FAILED_RE = /^Failed to (connect to (user )?bus|create bus connection|start transient scope unit)/m;

export function toolCall(ctx, cmd, args, opts = {}) {
  if (ctx.on == null || ctx.on === '') return { cmd, args };
  if (opts.isolate != null) {
    const { seconds, memoryMax, scope = true } = opts.isolate;
    return {
      cmd: 'sh',
      args: ['-c', REMOTE_ISOLATE_PREAMBLE, 'sh', String(seconds), String(memoryMax),
        scope ? 'scope' : 'plain', cmd, ...args],
    };
  }
  return { cmd: 'sh', args: ['-c', REMOTE_PREAMBLE, 'sh', cmd, ...args] };
}

// An isolated child killed with SIGKILL: past the deadline's grace (`timeout -k`) or at MemoryMax.
function killedError(isolate, result) {
  return {
    kind: 'failed',
    message: `killed at its ${isolate.memoryMax} memory cap or ${isolate.seconds}s deadline`
      + (result.stderr ? `: ${result.stderr.trim()}` : ''),
  };
}

export async function invoke(exec, ctx, cmd, args, opts = {}) {
  try {
    const { isolate, ...execOpts } = opts;
    const run = (iso) => {
      const call = toolCall(ctx, cmd, args, iso != null ? { isolate: iso } : {});
      return exec(call.cmd, call.args, { cwd: ctx.cwd, on: ctx.on, ...execOpts });
    };
    let result = await run(isolate);
    const remote = isolate != null && ctx.on != null && ctx.on !== '';
    if (remote && result.code !== 0 && SCOPE_FAILED_RE.test(result.stderr ?? '')) {
      result = await run({ ...isolate, scope: false });
    }
    if (remote && result.code === 137) return { ok: false, error: killedError(isolate, result) };
    const error = classifyCode(result);
    if (error) return { ok: false, error };
    return { ok: true, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
  } catch (err) {
    return { ok: false, error: classifyCaught(err, ctx.on, labelOf(cmd, args)) };
  }
}

export function readJson(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (err) {
    return { ok: false, error: { kind: 'bad-json', message: messageOf(err) } };
  }
}

// gh --paginate with --jq prints one JSON value per page, one per line.
export function readPages(text) {
  const pages = [];
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;
    const parsed = readJson(line);
    if (!parsed.ok) return parsed;
    pages.push(parsed.value);
  }
  return { ok: true, value: pages };
}

// Below herdr's 30 s exec cap so a hung remote read still settles.
export const REVIEW_DEADLINE_MS = 20_000;

function withDeadline(promise, deadlineMs) {
  if (!(deadlineMs > 0)) return promise;
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error('timed out reading the review');
      err.timedOut = true;
      reject(err);
    }, deadlineMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export async function readReview(readTextFile, path, on, exec = null, opts = {}) {
  const deadlineMs = opts.deadlineMs ?? REVIEW_DEADLINE_MS;
  const remote = on != null && on !== '';
  try {
    const direct = Promise.resolve().then(() => readTextFile(path, { on }));
    let pending;
    if (remote && exec != null) {
      const viaCat = Promise.resolve().then(() => exec('cat', [path], { on })).then((r) => {
        if (r?.code !== 0) throw new Error(`cat exited ${r?.code}`);
        return r.stdout ?? '';
      });
      pending = Promise.any([direct, viaCat]);
    } else {
      pending = direct;
    }
    const text = await withDeadline(pending, deadlineMs);
    return { ok: true, text: text ?? null };
  } catch (e) {
    if (e && e.timedOut) {
      return { ok: false, error: { kind: 'exec', message: 'timed out reading the review' } };
    }
    const err = e instanceof AggregateError ? e.errors[0] : e;
    if (on != null && on !== '') {
      return { ok: false, error: { kind: 'exec', command: 'read review', message: execReason(err) } };
    }
    return { ok: false, error: { kind: 'failed', message: messageOf(err) } };
  }
}

// `prior` is this machine's last good listing: pr is asked to answer `unchanged` against its
// stamp instead of rebuilding every row, and on that answer the prior result stands.

// ceiling: 768M for one isolated `pr board` child; raise if a single-repo
// board legitimately exceeds it (the remote child is OOM-killed under 768M).
export const BOARD_MEMORY_MAX = '768M';

// One `pr board` phase for one checkout. Resolves to {ok:true, doc} or {ok:true, unchanged:true},
// or {ok:false, error}; a document that is not the one asked for is an outdated `pr`, never a
// partial read. `stamp` is the prior answer's stamp, which pr answers `unchanged` against.
export async function loadBoard(exec, ctx, phase, opts = {}) {
  const timeoutMs = PHASE_TIMEOUT_MS[phase];
  const ran = await invoke(exec, ctx, 'pr', boardArgv(ctx.cwd, phase, opts), {
    timeout: timeoutMs,
    isolate: {
      seconds: Math.floor(timeoutMs / 1000) - 5,
      memoryMax: BOARD_MEMORY_MAX,
    },
  });
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  const doc = parsed.value;
  if (doc?.unchanged === true && doc.schema_version === 1 && doc.phase === phase) {
    return { ok: true, unchanged: true, stamp: doc.stamp };
  }
  const valid = doc?.schema_version === 1 && doc.phase === phase && 'reviews_root' in doc
    && Array.isArray(doc.rows) && doc.rows.every((r) => Number.isInteger(r.number) && r.presentation != null
      && ['issues', 'badges', 'row_badges'].every((k) => Array.isArray(r.presentation[k])));
  if (!valid) {
    return { ok: false, error: { kind: 'outdated', message: `pr board answered an unexpected document for ${phase}` } };
  }
  return { ok: true, doc };
}

export async function loadFiles(exec, ctx, slug, number) {
  const ran = await invoke(exec, ctx, 'gh', filesArgv(slug, number));
  if (!ran.ok) return ran;
  const parsed = readPages(ran.stdout);
  if (!parsed.ok) return parsed;
  const files = mapFiles(parsed.value, FILES_PER_PAGE);
  // Which files .gitattributes calls generated is git's to answer, from the checkout. Without a
  // checkout, or when git cannot say, nothing is marked and the counts stay whole.
  if (ctx.cwd == null) return { ok: true, files };
  const generated = new Set();
  for (const argv of attrArgvs(files)) {
    const attr = await invoke(exec, ctx, 'git', argv);
    if (!attr.ok) return { ok: true, files };
    for (const p of generatedPaths(attr.stdout)) generated.add(p);
  }
  return { ok: true, files: markGenerated(files, generated) };
}

function assembledPages(pageMap) {
  const files = [];
  for (const page of [...pageMap.keys()].sort((a, b) => a - b)) {
    files.push(...pageMap.get(page));
  }
  return files;
}

async function loadFilesPage(exec, ctx, slug, number, page) {
  const ran = await invoke(exec, ctx, 'gh', filesPageArgv(slug, number, page));
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  return { ok: true, files: mapFiles(parsed.value, FILES_PER_PAGE, page) };
}

async function markFilesGenerated(exec, ctx, files) {
  if (ctx.cwd == null) return files;
  const generated = new Set();
  for (const argv of attrArgvs(files)) {
    const attr = await invoke(exec, ctx, 'git', argv);
    if (!attr.ok) return files;
    for (const p of generatedPaths(attr.stdout)) generated.add(p);
  }
  return markGenerated(files, generated);
}

// Page 1 first so the Files tab can paint, then pages 2..N together. `total` is the listing's
// changed-files count; without a positive integer, loadFiles paginates as before.
export async function loadFilesIncremental(exec, ctx, slug, number, total, onUpdate) {
  if (!Number.isInteger(total) || total < 1) {
    const result = await loadFiles(exec, ctx, slug, number);
    onUpdate(result);
    return result;
  }
  const pageCount = Math.ceil(total / FILES_PER_PAGE);
  const pageMap = new Map();

  const first = await loadFilesPage(exec, ctx, slug, number, 1);
  if (!first.ok) {
    onUpdate(first);
    return first;
  }
  pageMap.set(1, first.files);
  onUpdate({
    ok: true,
    files: assembledPages(pageMap),
    loading: pageCount > 1,
    total,
  });

  if (pageCount > 1) {
    let pageError = null;
    await Promise.all(Array.from({ length: pageCount - 1 }, (_, i) => i + 2).map(async (page) => {
      const r = await loadFilesPage(exec, ctx, slug, number, page);
      if (!r.ok) {
        if (pageError == null) pageError = r.error?.message ?? 'failed';
        return;
      }
      pageMap.set(page, r.files);
      if (pageError == null) {
        onUpdate({
          ok: true,
          files: assembledPages(pageMap),
          loading: pageMap.size < pageCount,
          total,
        });
      }
    }));
    if (pageError != null) {
      const result = {
        ok: true,
        files: assembledPages(pageMap),
        loading: false,
        total,
        pageError,
      };
      onUpdate(result);
      return result;
    }
  }

  const files = await markFilesGenerated(exec, ctx, assembledPages(pageMap));
  const result = { ok: true, files, loading: false, total };
  onUpdate(result);
  return result;
}

// One file's patch, read from the page loadFiles saw it on. `patch: null` when GitHub has none
// (binary, or too large for the API). `cache` maps `${slug}#${number}#${page}` to that page's
// `{filename: patch}` object so a second file on the same page does not refetch.
export async function loadPatch(exec, ctx, slug, number, file, cache) {
  if (cache == null) {
    const ran = await invoke(exec, ctx, 'gh', patchArgv(slug, number, file.page, file.path));
    if (!ran.ok) return ran;
    const text = ran.stdout.replace(/\n$/, '');
    return { ok: true, patch: text === '' ? null : text };
  }
  const key = `${slug}#${number}#${file.page}`;
  let obj = cache.get(key);
  if (obj === undefined) {
    const ran = await invoke(exec, ctx, 'gh', pagePatchesArgv(slug, number, file.page));
    if (!ran.ok) return ran;
    const parsed = readJson(ran.stdout);
    if (!parsed.ok) return parsed;
    obj = parsed.value;
    cache.set(key, obj);
  }
  const patch = obj == null ? null : obj[file.path];
  return { ok: true, patch: patch == null || patch === '' ? null : patch };
}

export async function loadTerminals(exec, ctx) {
  try {
    const ran = await exec('hc', lsArgv(), { cwd: ctx.cwd });
    if (ran.code !== 0) return { ok: false, entries: [] };
    const ids = terminalsOf(readJson(ran.stdout).value).map((e) => e.id);
    const got = await Promise.all(ids.map(async (id) => {
      const r = await exec('hc', getArgv(id), { cwd: ctx.cwd });
      return r.code === 0 ? readJson(r.stdout).value : null;
    }));
    return { ok: true, entries: terminalsOf(got.filter((g) => g != null)) };
  } catch {
    return { ok: false, entries: [] };
  }
}

export function runningFor(entries, slug) {
  return busyMap(entries ?? [], slug);
}

// Comments cache is stale after this many milliseconds.
export const OWED_TTL_MS = 60000;

// True when owedAt is not a finite number, or now - owedAt is at least ttlMs.
export function owedDue(owedAt, now, ttlMs = OWED_TTL_MS) {
  if (!Number.isFinite(owedAt)) return true;
  return now - owedAt >= ttlMs;
}

export async function checkTools(exec, ctx) {
  const ran = await invoke(exec, ctx, 'sh', [
    '-c', 'command -v pr; command -v gh; command -v wt; true',
  ]);
  if (!ran.ok) return ran;
  const names = new Set();
  for (const line of ran.stdout.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    const parts = t.split('/');
    names.add(parts[parts.length - 1]);
  }
  return {
    ok: true,
    have: { pr: names.has('pr'), gh: names.has('gh'), wt: names.has('wt') },
  };
}
