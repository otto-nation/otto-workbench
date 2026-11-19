/**
 * Pure helpers for a tab-bar toggle that selects a panel-body child.
 * Imports findNode only.
 *
 * panelOn(root, child) -> the panel is showing and panel-body.active is child.
 *
 * syncWorkspacePanels(enabled, child) -> the workspace.panels list with child
 * included, or null when it is already there (caller skips setShared).
 * A missing or non-array enabled starts empty. "files" and "changes" are
 * dropped so herdr's tabs mod does not draw those buttons.
 *
 * clickSteps(on, child) -> hide the panel when on; otherwise select child and
 * show the panel. The caller exits full screen before running these.
 */
import { findNode } from "./fullscreen.js";

export function panelOn(root, child) {
  const panel = findNode(root, "panel");
  const body = findNode(root, "panel-body");
  return Boolean(panel && !panel.hidden && body?.active === child);
}

export function syncWorkspacePanels(enabled, child) {
  const raw = Array.isArray(enabled) ? enabled : [];
  const list = raw.filter((id) => id !== "files" && id !== "changes");
  if (list.includes(child) && list.length === raw.length) return null;
  if (list.includes(child)) return list;
  return [...list, child];
}

export function clickSteps(on, child) {
  if (on) return [{ command: "layout.hide", args: { id: "panel" } }];
  return [
    { command: "layout.select", args: { stack: "panel-body", child } },
    { command: "layout.show", args: { id: "panel" } },
  ];
}
