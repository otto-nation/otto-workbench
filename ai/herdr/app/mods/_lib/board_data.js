/**
 * Fetch and join team-PR board data through `exec`.
 *
 * `exec(cmd, args, {cwd, on})` returns `Promise<{code, stdout, stderr}>`
 * and rejects on a timeout, a program that cannot start, or an unreachable
 * machine. Every exported loader takes `exec` first and `ctx = {cwd, on}`;
 * none throw. Failures are
 * `{ok:false, error:{kind, message}}` with kind `missing-tool`,
 * `timeout`, `exec`, `auth`, `failed`, or `bad-json`.
 */

import { busyPrs } from './batch.js';
import { boardArgv, PHASE_TIMEOUT_MS } from './board_doc.js';
import { busyMap, getArgv, lsArgv, terminalsOf } from './busy.js';
import {
  batches,
  FILES_PER_PAGE,
  filesArgv,
  filesPageArgv,
  graphqlArgv,
  landedArgv,
  mapGraphql,
  mapPulls,
  pagePatchesArgv,
  patchArgv,
  pullsArgv,
  userArgv,
} from './github.js';
import { attrArgvs, generatedPaths, mapFiles, markGenerated } from './diff.js';
import { hostOf, remoteSlug } from './location.js';
import { joinReviews, keepLanded, landedCandidates } from './reviews.js';

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

// A command exec'd on another machine runs without a login shell, so ~/.local/bin (the workbench's
// `pr`) is missing from PATH and `pr` resolves to coreutils' pagination tool of the same name.
// Prefixing the workbench bin dir for remote calls makes the right `pr` win; local calls keep the
// app's own PATH.
// A remote terminal is started by the machine's core, not by a login shell, so it lacks what the
// shell's startup files export: the workbench bin dir and ~/.env.local (GOOGLE_CLOUD_PROJECT and
// the like, without which pi never registers its Vertex provider). ~/.env.local is sourced only
// after a subshell proves it parses under sh — a syntax error in a sourced file would otherwise
// end this shell before `exec`.
const REMOTE_ENV = 'PATH="$HOME/.local/bin:$PATH"; '
  + 'if [ -r "$HOME/.env.local" ] && ( . "$HOME/.env.local" ) >/dev/null 2>&1; '
  + 'then set -a; . "$HOME/.env.local" >/dev/null 2>&1; set +a; fi; ';

export const REMOTE_PREAMBLE = REMOTE_ENV + 'exec "$@"';

// Isolated remote children leave herdr-core serve's 512M cgroup (MemoryMax=512M,
// MemorySwapMax=0 on the transient unit `serve` starts for exec) so a heavy
// command cannot throttle the connection, and self-terminate at the deadline.
// `$1` is seconds, `$2` is MemoryMax; both are positional so they are not
// interpolated into this script. Usability is `command -v` plus the user bus
// socket systemd-run talks to — no extra probe process per call; without the
// bus it falls back to a bare `timeout` rather than failing the read.
export const REMOTE_ISOLATE_PREAMBLE = REMOTE_ENV
  + 'seconds=$1; memoryMax=$2; shift 2; '
  + 'if command -v systemd-run >/dev/null 2>&1 && [ -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then '
  + 'exec systemd-run --user --scope --quiet --collect -p MemoryMax="$memoryMax" -p MemorySwapMax=0 -- timeout -k 5 "$seconds" "$@"; '
  + 'elif command -v timeout >/dev/null 2>&1; then '
  + 'exec timeout -k 5 "$seconds" "$@"; '
  + 'else exec "$@"; fi';

export function toolCall(ctx, cmd, args, opts = {}) {
  if (ctx.on == null || ctx.on === '') return { cmd, args };
  if (opts.isolate != null) {
    const { seconds, memoryMax } = opts.isolate;
    return {
      cmd: 'sh',
      args: ['-c', REMOTE_ISOLATE_PREAMBLE, 'sh', String(seconds), String(memoryMax), cmd, ...args],
    };
  }
  return { cmd: 'sh', args: ['-c', REMOTE_PREAMBLE, 'sh', cmd, ...args] };
}

export async function invoke(exec, ctx, cmd, args, opts = {}) {
  try {
    const { isolate, ...execOpts } = opts;
    const call = toolCall(ctx, cmd, args, isolate != null ? { isolate } : {});
    const result = await exec(call.cmd, call.args, { cwd: ctx.cwd, on: ctx.on, ...execOpts });
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

function emptyEnrich() {
  return {
    isDraft: false,
    inMergeQueue: false,
    mergeable: 'UNKNOWN',
    ciStatus: 'none',
    ciUnknown: false,
    reviewDecision: null,
    additions: 0,
    deletions: 0,
    changedFiles: 0,
    latestReviews: [],
    requestedReviewers: [],
  };
}

function asOpen(pull, extra) {
  const e = extra ?? {};
  return {
    ...emptyEnrich(),
    ...pull,
    state: e.state ?? 'OPEN',
    isDraft: e.isDraft ?? pull.draft,
    inMergeQueue: e.inMergeQueue ?? false,
    mergeable: e.mergeable ?? 'UNKNOWN',
    ciStatus: e.ciStatus ?? 'none',
    // Enrichment missing: failed batch or null alias, so CI/mergeability/reviews unknown.
    ciUnknown: extra == null,
    reviewDecision: e.reviewDecision ?? null,
    additions: e.additions ?? 0,
    deletions: e.deletions ?? 0,
    changedFiles: e.changedFiles ?? 0,
    latestReviews: e.latestReviews ?? [],
    requestedReviewers: e.requestedReviewers ?? [],
  };
}

// Stay well under GitHub concurrency limits.
// ceiling: the mod has no process cap so fan-out is up to 4 per repo across all repos at once;
// if many repos on one remote hit ssh MaxSessions or GitHub secondary rate limits, add a per-target cap.
const GRAPHQL_CONCURRENCY = 4;

async function graphqlByNumber(exec, ctx, slug, numbers, argvOf) {
  let partial = false;
  const byNumber = {};
  const groups = batches(numbers);
  let next = 0;
  const workerCount = Math.min(GRAPHQL_CONCURRENCY, groups.length);
  async function worker() {
    while (true) {
      const i = next;
      next += 1;
      if (i >= groups.length) return;
      const batch = groups[i];
      const ran = await invoke(exec, ctx, 'gh', argvOf(slug, batch));
      if (!ran.ok) {
        partial = true;
        continue;
      }
      const parsed = readJson(ran.stdout);
      if (!parsed.ok) {
        partial = true;
        continue;
      }
      Object.assign(byNumber, mapGraphql(parsed.value, batch));
    }
  }
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return { byNumber, partial };
}

export async function loadRepo(exec, ctx) {
  if (ctx.cwd == null) {
    return { ok: false, error: { kind: 'failed', message: 'not in a repository' } };
  }
  const ran = await invoke(exec, ctx, 'git', ['remote', 'get-url', 'origin']);
  if (!ran.ok) return ran;
  const url = ran.stdout.trim();
  const slug = remoteSlug(url);
  const host = hostOf(url);
  if (slug == null) {
    return {
      ok: false,
      error: { kind: 'failed', message: 'origin is not a GitHub remote' },
    };
  }
  return { ok: true, slug, host };
}

export async function loadUser(exec, ctx) {
  const ran = await invoke(exec, ctx, 'gh', userArgv());
  if (!ran.ok) return ran;
  return { ok: true, login: ran.stdout.trim() };
}

export async function loadOpenPrs(exec, ctx, slug) {
  let ran;
  try {
    const call = toolCall(ctx, 'gh', pullsArgv(slug));
    ran = await exec(call.cmd, call.args, { cwd: ctx.cwd, on: ctx.on });
  } catch (err) {
    return { ok: false, error: classifyCaught(err, ctx.on, labelOf('gh', pullsArgv(slug))) };
  }
  const coded = classifyCode(ran);
  if (coded) return { ok: false, error: coded };
  const parsed = readPages(ran.stdout ?? '');
  if (!parsed.ok) return parsed;
  const pulls = mapPulls(parsed.value);
  const numbers = pulls.map((p) => p.number);
  const { byNumber, partial } = await graphqlByNumber(
    exec, ctx, slug, numbers, graphqlArgv,
  );
  return { ok: true, prs: pulls.map((p) => asOpen(p, byNumber[p.number])), partial };
}

// A review on another machine is read two ways at once, first answer wins: herdr's readTextFile,
// and `cat` through exec. Either alone can stall on a remote, which left the card on "Loading…".
export async function readReview(readTextFile, path, on, exec = null) {
  const remote = on != null && on !== '';
  try {
    const direct = Promise.resolve().then(() => readTextFile(path, { on }));
    let text;
    if (remote && exec != null) {
      const viaCat = Promise.resolve().then(() => exec('cat', [path], { on })).then((r) => {
        if (r?.code !== 0) throw new Error(`cat exited ${r?.code}`);
        return r.stdout ?? '';
      });
      text = await Promise.any([direct, viaCat]);
    } else {
      text = await direct;
    }
    return { ok: true, text: text ?? null };
  } catch (e) {
    const err = e instanceof AggregateError ? e.errors[0] : e;
    if (on != null && on !== '') {
      return { ok: false, error: { kind: 'exec', command: 'read review', message: execReason(err) } };
    }
    return { ok: false, error: { kind: 'failed', message: messageOf(err) } };
  }
}

// `prior` is this machine's last good listing: pr is asked to answer `unchanged` against its
// stamp instead of rebuilding every row, and on that answer the prior result stands.
export async function loadListing(exec, ctx, prior = null) {
  const stamp = prior?.listing?.stamp;
  const ran = await invoke(exec, ctx, 'pr', [
    'review', '--list', '--schema-version', '1', ...(stamp ? ['--if-changed', stamp] : []),
  ]);
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  if (parsed.value?.unchanged === true && prior != null) return prior;
  return { ok: true, listing: parsed.value, reviewsRoot: parsed.value.reviews_root };
}

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

export async function loadBatchDoc(exec, ctx) {
  let ran;
  try {
    const call = toolCall(ctx, 'pr', ['batch', 'status', '--full', '--offline']);
    ran = await exec(call.cmd, call.args, { cwd: ctx.cwd, on: ctx.on });
  } catch {
    return { ok: true, doc: null };
  }
  if (ran.code !== 0) return { ok: true, doc: null };
  try {
    return { ok: true, doc: JSON.parse(ran.stdout) };
  } catch {
    return { ok: true, doc: null };
  }
}

export async function loadBusy(exec, ctx, slug) {
  const ran = await loadBatchDoc(exec, ctx);
  return { ok: true, busy: busyPrs(ran.doc, slug) };
}

function listingRows(listing) {
  if (Array.isArray(listing)) return listing;
  if (listing != null && Array.isArray(listing.reviews)) return listing.reviews;
  return [];
}

function rowFor(listing, slug, host, number) {
  let best = null;
  for (const row of listingRows(listing)) {
    if (row.repo !== slug || row.pr_number !== number) continue;
    if (row.host != null && row.host !== '' && row.host !== host) continue;
    if (best == null || (row.reviewed_at ?? '') > (best.reviewed_at ?? '')) best = row;
  }
  return best;
}

function asLanded(number, extra, row) {
  const e = extra ?? {};
  return {
    number,
    title: `#${number}`,
    author: '',
    branch: row?.head_ref ?? '',
    baseBranch: '',
    draft: false,
    url: '',
    headSha: row?.head_sha ?? '',
    labels: [],
    createdAt: null,
    updatedAt: null,
    ...emptyEnrich(),
    ...e,
    state: 'MERGED',
    isDraft: e.isDraft ?? false,
  };
}

export async function loadLanded(exec, ctx, slug, listing, host, openPrs, nowMs) {
  const numbers = landedCandidates(openPrs, listing, slug, host);
  const { byNumber, partial } = await graphqlByNumber(
    exec, ctx, slug, numbers, landedArgv,
  );
  const merged = [];
  for (const n of numbers) {
    const extra = byNumber[n];
    if (extra == null) continue;
    merged.push(asLanded(n, extra, rowFor(listing, slug, host, n)));
  }
  return { ok: true, prs: keepLanded(merged, nowMs), partial };
}

// Live terminals the board or pane started. `hc ls` carries no labels, so each listed address
// is read with `hc get` (a handful at most). The local core also reports remote machines'
// terminals, so one pass covers every target. A failed listing is an empty map: a missing
// spinner must not fail the board.
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

// `owed` maps a PR number to what `pr` says it is owed an answer for (see owedFrom); a PR
// absent from it has nothing owed, or nothing known yet.
export function assemble({ openPrs, landedPrs, listing, slug, host, login, busy, running, owed }) {
  return joinReviews(
    [...(openPrs ?? []), ...(landedPrs ?? [])],
    listing, slug, host, login, running ?? new Map(),
  ).map((pr) => ({ ...pr, busy: busy.has(pr.number), owedFeedback: owed?.get(pr.number) ?? null }));
}

/** Argv after `pr` for the comments-only plan of a checkout: reads no branches. */
export function owedArgv(cwd) {
  return ['batch', 'plan', '--checkout', cwd, '--needs', 'comments'];
}

/**
 * PR number -> what it is owed, from a `pr batch plan` document, for the rows of `slug`.
 * Only rows whose comments need is set; the reason is pr.owed_feedback's own summary, so the
 * board says exactly what `pr comments` would answer.
 */
export function owedFrom(doc, slug) {
  const out = new Map();
  const want = String(slug ?? '').toLowerCase();
  for (const row of doc?.rows ?? []) {
    if (String(row?.repo ?? '').toLowerCase() !== want || !Number.isInteger(row?.pr)) continue;
    const need = row?.needs?.comments;
    if (need?.needed) out.set(row.pr, String(need.reason ?? ''));
  }
  return out;
}

export async function loadOwed(exec, ctx, slug) {
  const ran = await invoke(exec, ctx, 'pr', owedArgv(ctx.cwd));
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  return { ok: true, owed: owedFrom(parsed.value, slug) };
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
