/**
 * Pure helper for "Open board": find the board tab that already exists. Imports nothing.
 *
 * boardTabId(spaces, name) -> id of a tab called `name` in the active space, else in any space, else null.
 * Only a tab someone named counts, so a numbered tab is never mistaken for the board.
 */
export function boardTabId(spaces, name) {
  const named = (s) => (s.tabs ?? []).find((t) => t.named && t.name === name);
  const active = (spaces ?? []).find((s) => s.active);
  const hit = (active && named(active)) || (spaces ?? []).map(named).find(Boolean);
  return hit ? hit.id : null;
}
