/**
 * Pure helpers for the PRs panel full-screen toggle. Imports nothing.
 *
 * A node's `hidden` is absent (undefined) when shown, never `false`. Test truthiness.
 *
 * findNode(root, id) -> the node with that id, depth-first over `.children`, else null.
 *
 * isPanelCopy(props) -> true for the copy the workbench layout mounts in the side panel
 * (`props.placement === "panel"`).
 *
 * fullscreenState(root) -> "off" | "on" | "stranded".
 * "off" when there is no root, `main` is missing, or `main` is not hidden.
 * When `main.hidden` is truthy: "on" if `panel` exists, is not hidden, and
 * `panel-body.active` is "prs-panel"; otherwise "stranded".
 * Only "on" and "off" are stable: "stranded" must be healed by a caller (panel_button does,
 * via healDue) or the main area stays hidden.
 *
 * panelShown(root) -> whether the panel copy is on screen: `panel` not hidden and
 * `panel-body.active` is "prs-panel". No root reads as shown (nothing says otherwise).
 *
 * enterPlan(root) -> { savedPx, steps }. savedPx is panel.size.px when that is a
 * finite number > 0, else null. steps hide `main` then resize `panel` to weight 1.
 *
 * exitSteps(savedPx) -> show `main`, then resize `panel` to savedPx only when that
 * is a finite number > 0. With no saved width, only show `main`.
 *
 * healDue(state, pendingUntil, now) -> true only when state is "stranded" and
 * now >= pendingUntil. A missing, null, or NaN pendingUntil counts as 0.
 */
export function findNode(root, id) {
  if (root?.id === id) return root;
  for (const child of root?.children ?? []) {
    const found = findNode(child, id);
    if (found) return found;
  }
  return null;
}

export function isPanelCopy(props) {
  return props?.placement === "panel";
}

export function fullscreenState(root) {
  if (!root) return "off";
  const main = findNode(root, "main");
  if (!main || !main.hidden) return "off";
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  if (panel && !panel.hidden && body?.active === "prs-panel") return "on";
  return "stranded";
}

export function panelShown(root) {
  if (!root) return true;
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  return Boolean(panel && !panel.hidden && body?.active === "prs-panel");
}

export function enterPlan(root) {
  const px = findNode(root, "panel")?.size?.px;
  const savedPx = Number.isFinite(px) && px > 0 ? px : null;
  return {
    savedPx,
    steps: [
      { command: "layout.hide", args: { id: "main" } },
      { command: "layout.resize", args: { id: "panel", weight: 1 } },
    ],
  };
}

export function exitSteps(savedPx) {
  const steps = [{ command: "layout.show", args: { id: "main" } }];
  if (Number.isFinite(savedPx) && savedPx > 0) {
    steps.push({ command: "layout.resize", args: { id: "panel", px: savedPx } });
  }
  return steps;
}

export function healDue(state, pendingUntil, now) {
  const pending = Number.isFinite(pendingUntil) ? pendingUntil : 0;
  return state === "stranded" && now >= pending;
}
