/**
 * Compact view selection for the PRs board.
 */
import { BREAKPOINT } from "./tokens.js";

// The block's measured width picks the density: a narrow panel gets the compact list.
export function viewOf(width) {
  return (width ?? Infinity) < BREAKPOINT.compact ? "compact" : "board";
}

// A lane with PRs, or one whose data is still loading or failed (its state is the section's content).
// A group without a status is ready.
export function compactSections(groups) {
  return (groups ?? [])
    .filter((g) => (g.items ?? []).length > 0 || (g.status ?? 'ready') !== 'ready')
    .map((g) => ({
      key: g.key, label: g.label, count: (g.items ?? []).length, items: g.items ?? [],
      batch: g.batch ?? null, status: g.status ?? 'ready', message: g.message ?? null,
    }));
}
