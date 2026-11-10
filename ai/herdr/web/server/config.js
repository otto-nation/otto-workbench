/**
 * PR board configuration, read once from the environment (spec § 8.3).
 *
 * loadConfig(env) returns a frozen object or throws ConfigError naming the variable; main.js turns
 * that into exit 2. An empty string counts as unset for every variable.
 */
import { join } from 'node:path';

export class ConfigError extends Error {}

export const MIN_TOKEN_LENGTH = 32;
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost']);

const val = (env, k) => (env[k] === undefined || env[k] === null || env[k] === '' ? null : String(env[k]));

function intIn(env, k, lo, hi, dflt) {
  const raw = val(env, k);
  if (raw == null) {
    if (dflt === undefined) throw new ConfigError(`${k} is required`);
    return dflt;
  }
  if (!/^[0-9]+$/.test(raw)) throw new ConfigError(`${k} must be an integer, got ${raw}`);
  const n = Number(raw);
  if (n < lo || n > hi) throw new ConfigError(`${k} must be between ${lo} and ${hi}, got ${raw}`);
  return n;
}

export function loadConfig(env) {
  const token = val(env, 'PR_BOARD_TOKEN') ?? '';
  if (token.length < MIN_TOKEN_LENGTH) {
    throw new ConfigError(`PR_BOARD_TOKEN must be at least ${MIN_TOKEN_LENGTH} characters`);
  }
  const host = val(env, 'PR_BOARD_HOST') ?? '127.0.0.1';
  if (!LOOPBACK.has(host)) throw new ConfigError(`PR_BOARD_HOST must be 127.0.0.1, ::1 or localhost, got ${host}`);
  const port = intIn(env, 'PR_BOARD_PORT', 0, 65535);
  const origin = val(env, 'PR_BOARD_ORIGIN');
  if (origin != null && !/^https?:\/\/[^/\s]+$/.test(origin)) {
    throw new ConfigError(`PR_BOARD_ORIGIN must be scheme://host[:port] with no path, got ${origin}`);
  }
  const ovisUrl = val(env, 'PR_BOARD_OVIS_URL');
  if (ovisUrl != null && !/^https?:\/\/\S+$/.test(ovisUrl)) {
    throw new ConfigError(`PR_BOARD_OVIS_URL must be an http(s) URL, got ${ovisUrl}`);
  }
  const home = val(env, 'HOME');
  const stateRoot = val(env, 'XDG_STATE_HOME') ?? (home ? join(home, '.local', 'state') : null);
  const stateDir = val(env, 'PR_BOARD_STATE_DIR') ?? (stateRoot ? join(stateRoot, 'pr-board') : null);
  if (stateDir == null) throw new ConfigError('PR_BOARD_STATE_DIR is required when HOME is unset');
  return Object.freeze({
    token, host, port, origin, ovisUrl, stateDir,
    hcBin: val(env, 'PR_BOARD_HC') ?? 'hc',
    name: val(env, 'PR_BOARD_NAME') ?? 'this server',
    fullMs: intIn(env, 'PR_BOARD_FULL_REFRESH_S', 30, 86400, 180) * 1000,
    listingMs: intIn(env, 'PR_BOARD_LISTING_REFRESH_S', 5, 3600, 15) * 1000,
    maxProcs: intIn(env, 'PR_BOARD_MAX_PROCS', 1, 32, 4),
  });
}
