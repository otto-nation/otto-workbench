/**
 * The PR board's HTTP surface (spec § 5.5) as one fetch(Request) → Response, so tests drive it
 * without a socket. Order: health and sign-in (no token) → token gate → CSRF for POST → routes.
 * Phases 2/3 add routes through `routes` without touching this file's gate.
 */
import { readFile } from 'node:fs/promises';
import { readReview } from '../../app/mods/_lib/board_data.js';
import {
  authenticated, clearedCookie, createGuessBudget, hasCredential, isJson, isSecure, sameOrigin, sessionCookie, tokenMatches,
} from './auth.js';
import { TYPES } from './static.js';

export const SECURITY_HEADERS = {
  'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'cross-origin-opener-policy': 'same-origin',
  'cross-origin-resource-policy': 'same-origin',
};
const HTML = 'text/html; charset=utf-8';
const JSON_TYPE = 'application/json; charset=utf-8';
const MAX_BODY = 16384;
// GitHub owners are letters, digits and hyphens; a repo name may hold dots but is never . or ..
export const REPO_RE = /^[A-Za-z0-9-]+\/(?!\.\.?$)[A-Za-z0-9_.-]+$/;
export const VIEWS = Object.freeze(['team', 'mine']);

export function respond(body, { status = 200, type = JSON_TYPE, headers = {} } = {}) {
  const h = new Headers({ ...SECURITY_HEADERS, 'content-type': type, ...headers });
  if (type === JSON_TYPE) h.set('cache-control', 'no-store');
  else if (!h.has('cache-control')) h.set('cache-control', 'no-cache');
  // A 204 may carry no body at all; the Response constructor throws on an empty string.
  if (status === 204) return new Response(null, { status, headers: h });
  return new Response(type === JSON_TYPE ? JSON.stringify(body) : body, { status, headers: h });
}
const json = (body, status = 200, headers = {}) => respond(body, { status, headers });

export function prRef(params) {
  const repo = params.get('repo') ?? '';
  const raw = params.get('number') ?? '';
  if (!REPO_RE.test(repo) || !/^[0-9]+$/.test(raw) || Number(raw) <= 0) return null;
  return { repo, number: Number(raw) };
}

export async function readBody(request) {
  const text = await request.text();
  if (text.length > MAX_BODY) return { ok: false };
  return { ok: true, text };
}

export function createApp({
  config, board, hub, files, readTextFile, statics, pages, version,
  budget = createGuessBudget(), sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  onSubscribe = (_view) => {}, routes = [],
}) {
  const unauthorized = (request) => {
    const wantsHtml = request.method === 'GET' && (request.headers.get('accept') ?? '').includes('text/html');
    return wantsHtml ? respond(pages.login(''), { status: 401, type: HTML }) : json({ error: 'token_required' }, 401);
  };

  async function login(request) {
    if (!sameOrigin(request, config.origin)) return json({ error: 'forbidden' }, 403);
    const body = await readBody(request);
    if (!body.ok) return json({ error: 'too_large' }, 413);
    const token = new URLSearchParams(body.text).get('token') ?? '';
    const wait = budget.delayMs();
    if (wait) await sleep(wait);
    if (!tokenMatches(token, config.token)) {
      budget.fail();
      return respond(pages.login('That token is not right.'), { status: 401, type: HTML });
    }
    budget.reset();
    return respond('', { status: 303, type: HTML, headers: { location: '/', 'set-cookie': sessionCookie(config.token, isSecure(request)) } });
  }

  // PR-scoped GETs: the PR must be on the board; its cwd/on come from the board's own record.
  function withPr(url, fn) {
    const ref = prRef(url.searchParams);
    if (!ref) return json({ error: 'bad_request' }, 400);
    const pr = board.findPr(ref.repo, ref.number);
    if (!pr) return json({ error: 'not_found' }, 404);
    return fn(pr);
  }

  // Stamp → path is resolved from findPr's internal record (review_file is stripped from the
  // snapshot). readTextFile then refuses anything outside the board's reviews root.
  async function review(url) {
    return withPr(url, async (pr) => {
      const stamp = url.searchParams.get('stamp') ?? '';
      const run = (pr.reviewRuns ?? []).map((r) => r.run).find((r) => r && (r.stamp ?? '') === stamp);
      if (!run?.review_file) return json({ error: 'not_found' }, 404);
      return json(await readReview(readTextFile, run.review_file, pr.on ?? null));
    });
  }

  async function staticFile(pathname) {
    if (pathname === '/') return respond(pages.index, { type: HTML });
    if (pathname === '/gen/theme.css') return respond(pages.themeCss, { type: TYPES['.css'] });
    if (pathname === '/gen/tokens.css') return respond(pages.tokensCss, { type: TYPES['.css'] });
    const file = statics.get(pathname);
    if (!file) return null;
    const ext = pathname.slice(pathname.lastIndexOf('.'));
    return respond(await readFile(file), { type: TYPES[ext] });
  }

  async function fetch(request) {
    const url = new URL(request.url);
    const { pathname } = url;
    const { method } = request;
    if (method === 'GET' && pathname === '/healthz') return json({ ok: true, version });
    // The sign-in page is shown before any session exists, so its stylesheet cannot sit behind the gate.
    if (method === 'GET' && pathname === '/login.css') return respond(pages.loginCss, { type: TYPES['.css'] });
    if (pathname === '/login') {
      if (method === 'GET') return respond(pages.login(''), { type: HTML });
      if (method === 'POST') return login(request);
      return json({ error: 'method_not_allowed' }, 405);
    }
    if (!authenticated(request, config.token)) {
      if (hasCredential(request)) {
        budget.fail();
        const wait = budget.delayMs();
        if (wait) await sleep(wait);
      }
      return unauthorized(request);
    }
    if (method === 'POST') {
      if (!sameOrigin(request, config.origin) || !isJson(request)) return json({ error: 'forbidden' }, 403);
      if (pathname === '/logout') return respond('', { status: 204, type: HTML, headers: { 'set-cookie': clearedCookie() } });
      if (pathname === '/api/refresh') {
        board.refresh({ fresh: true });
        return json({ ok: true }, 202);
      }
    }
    if (method === 'GET') {
      // Spec § 5.5/§ 7 name only /healthz, /login, /login.css, POST /login as public.
      // /favicon.ico stays behind the token so a probe without one is 401; 204 stops the browser 404.
      if (pathname === '/favicon.ico') return respond(null, { status: 204, type: HTML });
      if (pathname === '/api/board') return json(board.snapshot());
      if (pathname === '/api/events') {
        // The page's tab rides its subscription: it lives exactly as long as the stream (spec § 5.5).
        const view = url.searchParams.get('view') ?? 'team';
        if (!VIEWS.includes(view)) return json({ error: 'bad_request' }, 400);
        const res = hub.subscribe([['board', board.snapshot()]], { view });
        onSubscribe(view);
        return res;
      }
      if (pathname === '/api/review') return review(url);
      if (pathname === '/api/files') return withPr(url, async (pr) => json(await files.files(pr)));
      if (pathname === '/api/patch') {
        return withPr(url, async (pr) => {
          const r = await files.patch(pr, url.searchParams.get('path') ?? '');
          return json(r, r.notFound ? 404 : 200);
        });
      }
    }
    const route = routes.find((r) => r.method === method && r.path === pathname);
    if (route) return route.handler(request, url);
    if (method === 'GET') {
      const res = await staticFile(pathname);
      if (res) return res;
    }
    return json({ error: 'not_found' }, 404);
  }

  return { fetch };
}
