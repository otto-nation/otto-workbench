/**
 * Pure helpers for a tab-bar toggle that selects a panel-body child or group.
 * Imports findNode only.
 *
 * panelOn(root, child) -> the panel is showing and panel-body.active is child.
 *
 * panelActive(root) -> panel-body.active when the panel is showing, else null.
 *
 * groupOn(root, children) -> the panel is showing and active is in children.
 *
 * groupClickSteps(active, hub, children) -> select hub + show panel when off,
 * hide panel when active is hub, else select hub without hiding.
 *
 * enableWorkspacePanels(enabled, children) -> the workspace.panels list with
 * each child included, or null when nothing changes. A missing or non-array
 * enabled starts empty. "files" and "changes" are dropped so herdr's tabs mod
 * does not draw those buttons.
 */
import { findNode } from "./fullscreen.js";

export function panelOn(root, child) {
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  return Boolean(panel && !panel.hidden && body?.active === child);
}

export function panelActive(root) {
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  if (!panel || panel.hidden) return null;
  return body?.active ?? null;
}

export function groupOn(root, children) {
  const active = panelActive(root);
  return active != null && (children ?? []).includes(active);
}

export function groupClickSteps(active, hub, children) {
  if (active == null || !(children ?? []).includes(active)) {
    return [
      { command: "layout.select", args: { stack: "panel-body", child: hub } },
      { command: "layout.show", args: { id: "panel" } },
    ];
  }
  if (active === hub) return [{ command: "layout.hide", args: { id: "panel" } }];
  return [{ command: "layout.select", args: { stack: "panel-body", child: hub } }];
}

export function enableWorkspacePanels(enabled, children) {
  const raw = Array.isArray(enabled) ? enabled : [];
  const next = raw.filter((id) => id !== "files" && id !== "changes");
  let changed = next.length !== raw.length;
  for (const child of children ?? []) {
    if (!next.includes(child)) {
      next.push(child);
      changed = true;
    }
  }
  return changed ? next : null;
}
