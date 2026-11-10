/**
 * Pure helpers for "Open board". Imports nothing.
 *
 * boardTabId(spaces, name) -> id of a tab called `name` in the active space, else in any space, else null.
 * Only a tab someone named counts, so a numbered tab is never mistaken for the board.
 *
 * offersOpenBoard(props) -> true for the copy the workbench layout mounts in the side panel
 * (`props.placement === "panel"`), whatever its width; false for the board tab itself.
 */
export function boardTabId(spaces, name) {
  const named = (s) => (s.tabs ?? []).find((t) => t.named && t.name === name);
  const active = (spaces ?? []).find((s) => s.active);
  const hit = (active && named(active)) || (spaces ?? []).map(named).find(Boolean);
  return hit ? hit.id : null;
}

export function offersOpenBoard(props) {
  return props?.placement === "panel";
}
