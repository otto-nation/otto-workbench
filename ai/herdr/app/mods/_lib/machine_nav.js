/**
 * Machine navigation shared by the Queue and Hosts panes: which machine's page is open. Pure.
 */

/** Stable identity for a machine entry: `key` when set, else `on`, else ''. */
export function machineKey(entry) {
  if (entry?.key != null) return String(entry.key);
  if (entry?.on == null || entry.on === '') return '';
  return String(entry.on);
}

/** Nav state from a raw shared value: '' is the local machine, null/undefined is home. */
export function navStateOf(machine) {
  if (machine == null) return { machine: null };
  return { machine };
}

/** Open that machine's page: `{ ...state, machine: key ?? null }`. */
export function selectMachine(state, key) {
  return { ...(state ?? {}), machine: key ?? null };
}

/** Return to the machines home: `{ ...state, machine: null }`. */
export function clearMachine(state) {
  return { ...(state ?? {}), machine: null };
}

/** The entry whose key matches `state.machine`, or null when none / home. */
export function pageOf(state, machines) {
  const key = state?.machine;
  if (key == null) return null;
  const want = String(key);
  for (const entry of machines ?? []) {
    if (machineKey(entry) === want) return entry;
  }
  return null;
}
