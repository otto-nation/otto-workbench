/**
 * Who gets in: holders of the instance token, as a session cookie set by POST /login or as
 * `Authorization: Bearer`. Loopback earns nothing (spec § 7). Comparisons are constant-time over
 * SHA-256 digests; the cookie carries an HMAC of the token, never the token.
 */
import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

export const COOKIE = 'pr_board_session';
export const COOKIE_MAX_AGE_S = 31536000;
const SESSION_LABEL = 'pr-board-session-v1';
const BEARER = 'Bearer ';

const digest = (s) => createHash('sha256').update(String(s)).digest();

export function tokenMatches(candidate, token) {
  return typeof candidate === 'string' && timingSafeEqual(digest(candidate), digest(token));
}

export function sessionValue(token) {
  return createHmac('sha256', token).update(SESSION_LABEL).digest('hex');
}

// Malformed pairs are skipped: another app's junk cookie must not lock the owner out.
export function parseCookies(header) {
  const out = new Map();
  for (const part of String(header ?? '').split(';')) {
    const eq = part.indexOf('=');
    if (eq <= 0) continue;
    try {
      out.set(part.slice(0, eq).trim(), decodeURIComponent(part.slice(eq + 1).trim()));
    } catch { /* undecodable value: ignored */ }
  }
  return out;
}

export function hasCredential(request) {
  return parseCookies(request.headers.get('cookie')).has(COOKIE) || (request.headers.get('authorization') ?? '') !== '';
}

export function authenticated(request, token) {
  const cookie = parseCookies(request.headers.get('cookie')).get(COOKIE);
  if (cookie !== undefined && tokenMatches(cookie, sessionValue(token))) return true;
  const auth = request.headers.get('authorization') ?? '';
  return auth.startsWith(BEARER) && tokenMatches(auth.slice(BEARER.length), token);
}

export function isSecure(request) {
  return request.headers.get('x-forwarded-proto') === 'https' || new URL(request.url).protocol === 'https:';
}

export function sessionCookie(token, secure) {
  return `${COOKIE}=${sessionValue(token)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${COOKIE_MAX_AGE_S}${secure ? '; Secure' : ''}`;
}

export function clearedCookie() {
  return `${COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0`;
}

// A mutating request must come from this page: its Origin is the configured one, or names the Host
// it was sent to, and fetch metadata, when the browser sends it, says same-origin.
export function sameOrigin(request, configured) {
  const origin = request.headers.get('origin');
  if (!origin) return false;
  const site = request.headers.get('sec-fetch-site');
  if (site != null && site !== 'same-origin') return false;
  if (configured) return origin === configured;
  try {
    return new URL(origin).host === request.headers.get('host');
  } catch {
    return false;
  }
}

// A JSON body cannot be sent cross-origin without a preflight, which this server never answers.
export function isJson(request) {
  return /^application\/json(\s*;|$)/i.test(request.headers.get('content-type') ?? '');
}

// ceiling: one global budget, because behind Caddy every request comes from loopback; key it by the
// proxy's client address if the board is ever reachable without a proxy that sets it reliably.
export function createGuessBudget({ now = Date.now, free = 5, windowMs = 600000, maxDelayMs = 60000 } = {}) {
  let failures = [];
  // Delay is min(maxDelayMs, 1000 * 2^(n-free)); once n reaches this cap the delay is already
  // maxDelayMs, so further timestamps cannot change it. Defaults: 5 + ceil(log2(60)) = 11.
  const cap = free + Math.ceil(Math.log2(Math.max(maxDelayMs / 1000, 1)));
  const prune = (t) => { failures = failures.filter((f) => t - f < windowMs); };
  return {
    delayMs() {
      prune(now());
      const over = failures.length - free;
      return over < 0 ? 0 : Math.min(maxDelayMs, 1000 * 2 ** over);
    },
    fail() {
      const t = now();
      prune(t);
      if (failures.length >= cap) return;
      failures.push(t);
    },
    reset() { failures = []; },
    size() { return failures.length; },
  };
}
