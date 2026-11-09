/**
 * Fetch and join team-PR board data through `exec`.
 *
 * `exec(cmd, args, {cwd, on})` returns `Promise<{code, stdout, stderr}>`
 * and rejects when the machine is unreachable. Every exported loader
 * takes `exec` first and `ctx = {cwd, on}`; none throw. Failures are
 * `{ok:false, error:{kind, message}}` with kind `missing-tool`,
 * `unreachable`, `auth`, `failed`, or `bad-json`.
 */

import { busyPrs } from './batch.js';
import { busyMap, getArgv, lsArgv, terminalsOf } from './busy.js';
import {
  batches,
  graphqlArgv,
  landedArgv,
  mapGraphql,
  mapPulls,
  pullsArgv,
  userArgv,
} from './github.js';
import { hostOf, remoteSlug } from './location.js';
import { joinReviews, keepLanded, landedCandidates } from './reviews.js';

function messageOf(err) {
  if (err == null) return '';
  if (typeof err === 'string') return err;
  if (typeof err.message === 'string') return err.message;
  return String(err);
}

function classifyCaught(err, on) {
  const kind = on != null && on !== '' ? 'unreachable' : 'missing-tool';
  return { kind, message: messageOf(err) };
}

export function classifyCode(result) {
  const stderr = result.stderr ?? '';
  if (result.code === 127) {
    return { kind: 'missing-tool', message: stderr || 'command not found' };
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
  if (result.code !== 0) {
    return { kind: 'failed', message: stderr || `exit ${result.code}` };
  }
  return null;
}

// A command exec'd on another machine runs without a login shell, so ~/.local/bin (the workbench's
// `pr`) is missing from PATH and `pr` resolves to coreutils' pagination tool of the same name.
// Prefixing the workbench bin dir for remote calls makes the right `pr` win; local calls keep the
// app's own PATH.
export function toolCall(ctx, cmd, args) {
  if (ctx.on == null || ctx.on === '') return { cmd, args };
  return { cmd: 'sh', args: ['-c', 'PATH="$HOME/.local/bin:$PATH"; exec "$@"', 'sh', cmd, ...args] };
}

async function invoke(exec, ctx, cmd, args) {
  try {
    const call = toolCall(ctx, cmd, args);
    const result = await exec(call.cmd, call.args, { cwd: ctx.cwd, on: ctx.on });
    const error = classifyCode(result);
    if (error) return { ok: false, error };
    return { ok: true, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
  } catch (err) {
    return { ok: false, error: classifyCaught(err, ctx.on) };
  }
}

export function readJson(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (err) {
    return { ok: false, error: { kind: 'bad-json', message: messageOf(err) } };
  }
}

function emptyEnrich() {
  return {
    isDraft: false,
    inMergeQueue: false,
    mergeable: 'UNKNOWN',
    ciStatus: 'none',
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
    reviewDecision: e.reviewDecision ?? null,
    additions: e.additions ?? 0,
    deletions: e.deletions ?? 0,
    changedFiles: e.changedFiles ?? 0,
    latestReviews: e.latestReviews ?? [],
    requestedReviewers: e.requestedReviewers ?? [],
  };
}

async function graphqlByNumber(exec, ctx, slug, numbers, argvOf) {
  let partial = false;
  const byNumber = {};
  for (const batch of batches(numbers, 20)) {
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
    return { ok: false, error: classifyCaught(err, ctx.on) };
  }
  const coded = classifyCode(ran);
  if (coded) return { ok: false, error: coded };
  const parsed = readJson(ran.stdout ?? '');
  if (!parsed.ok) return parsed;
  const pulls = mapPulls(parsed.value);
  const numbers = pulls.map((p) => p.number);
  const { byNumber, partial } = await graphqlByNumber(
    exec, ctx, slug, numbers, graphqlArgv,
  );
  return { ok: true, prs: pulls.map((p) => asOpen(p, byNumber[p.number])), partial };
}

export async function readReview(readTextFile, path, on) {
  try {
    const text = await readTextFile(path, { on });
    return { ok: true, text: text ?? null };
  } catch (err) {
    const kind = on != null && on !== '' ? 'unreachable' : 'failed';
    return { ok: false, error: { kind, message: messageOf(err) } };
  }
}

export async function loadListing(exec, ctx) {
  const ran = await invoke(exec, ctx, 'pr', [
    'review', '--list', '--schema-version', '1',
  ]);
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  return { ok: true, listing: parsed.value, reviewsRoot: parsed.value.reviews_root };
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

export function assemble({ openPrs, landedPrs, listing, slug, host, login, busy, running }) {
  return joinReviews(
    [...(openPrs ?? []), ...(landedPrs ?? [])],
    listing, slug, host, login, running ?? new Map(),
  ).map((pr) => ({ ...pr, busy: busy.has(pr.number) }));
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
