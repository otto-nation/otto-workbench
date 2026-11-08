/**
 * Compact / rail view selection for the team-prs board.
 */

export function findNode(tree, id) {
  if (tree == null || typeof tree !== "object") return null;
  if (tree.id === id) return tree;
  for (const child of tree.children ?? []) {
    const found = findNode(child, id);
    if (found) return found;
  }
  return null;
}

export function isRail(tree) {
  return (findNode(tree, "sidebar")?.size?.px ?? Infinity) < 160;
}

// Props say where the copy lives; width says how much room a board pane has.
export function viewOf(props, width, tree) {
  if (props?.view === "sidebar") return isRail(tree) ? "rail" : "compact";
  if ((width ?? Infinity) < 560) return "compact";
  return "board";
}

export function compactSections(groups) {
  return (groups ?? [])
    .filter((g) => (g.items ?? []).length > 0)
    .map((g) => ({ key: g.key, label: g.label, count: g.items.length, items: g.items }));
}
