/**
 * host-health through `exec`. Pure: argv, parse, loaders.
 *
 * loadHealth shapes:
 *   {ok:true, health}   parsed JSON object with an object `load` and a `procs` key
 *   {ok:false, error}   any other failure
 * A document that is not that shape is an outdated `host-health`, never a partial read.
 */

import { invoke, readJson } from './board_data.js';

export async function loadHealth(exec, ctx) {
  const ran = await invoke(exec, ctx, 'host-health', ['--json']);
  if (!ran.ok) return ran;
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  const v = parsed.value;
  const load = v != null && typeof v === 'object' ? v.load : null;
  if (
    v == null
    || typeof v !== 'object'
    || Array.isArray(v)
    || load == null
    || typeof load !== 'object'
    || Array.isArray(load)
    || !('procs' in v)
  ) {
    return {
      ok: false,
      error: { kind: 'outdated', message: 'host-health answered an unexpected document' },
    };
  }
  return { ok: true, health: v };
}
