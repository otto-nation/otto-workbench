/**
 * Compact view selection for the team-prs board.
 */
import { BREAKPOINT } from "./tokens.js";

// The block's measured width picks the density: a narrow panel gets the compact list.
export function viewOf(width) {
  return (width ?? Infinity) < BREAKPOINT.compact ? "compact" : "board";
}

export function compactSections(groups) {
  return (groups ?? [])
    .filter((g) => (g.items ?? []).length > 0)
    .map((g) => ({ key: g.key, label: g.label, count: g.items.length, items: g.items }));
}
