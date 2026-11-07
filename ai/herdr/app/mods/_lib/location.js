/**
 * Location helpers for herdr-app: operator machine/cwd, git remote identity,
 * and untrusted-home detection. Pure values; no imports.
 */

export function locate(spaces, panes, focused) {
  const space = spaces.find((s) => s.active) ?? null;
  const pane = focused?.pane
    ? panes.find((p) => p.pane === focused.pane)
    : null;
  const on =
    focused?.pane === null
      ? focused.terminal?.includes('/')
        ? focused.terminal.split('/')[0]
        : null
      : (space?.machine ?? null);
  const cwd = pane?.cwd || focused?.cwd || space?.folder || null;
  return { on, cwd, space };
}

export function stripMachine(id) {
  const i = id.indexOf('/');
  return i === -1 ? id : id.slice(i + 1);
}

export function addr(on, id) {
  return on == null ? id : on + '/' + stripMachine(id);
}

function parseRemote(url) {
  if (typeof url !== 'string') return { host: null, slug: null };
  let s = url.trim().replace(/\/+$/, '');
  if (!s) return { host: null, slug: null };
  let host = null;
  let path = '';
  if (s.startsWith('git@')) {
    const colon = s.indexOf(':');
    if (colon === -1) return { host: null, slug: null };
    host = s.slice(4, colon);
    path = s.slice(colon + 1);
  } else if (/^(https?|ssh):\/\//.test(s)) {
    try {
      const u = new URL(s);
      host = u.hostname || null;
      path = u.pathname.replace(/^\/+/, '');
    } catch {
      return { host: null, slug: null };
    }
  } else {
    return { host: null, slug: null };
  }
  path = path.replace(/\.git$/, '').replace(/\/+$/, '');
  const parts = path.split('/').filter(Boolean);
  const slug = parts.length === 2 ? parts[0] + '/' + parts[1] : null;
  return { host: host || null, slug };
}

export function remoteSlug(url) {
  return parseRemote(url).slug;
}

export function hostOf(url) {
  return parseRemote(url).host;
}

export const UNTRUSTED_HOME_SUFFIX = '/.config/herdr-app-untrusted';

export function isUntrustedHome(appHome) {
  const s = String(appHome ?? '').replace(/\/+$/, '');
  return s.endsWith(UNTRUSTED_HOME_SUFFIX);
}
