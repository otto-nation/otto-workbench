/**
 * Pure helpers for a tab-bar toggle that selects a panel-body child.
 * Imports findNode only.
 *
 * panelOn(root, child) -> the panel is showing and panel-body.active is child.
 *
 * enableWorkspacePanel(enabled, child) -> the workspace.panels list with child
 * included, or null when it is already there (caller skips setShared).
 * A missing or non-array enabled starts from ["files", "changes"].
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

export function enableWorkspacePanel(enabled, child) {
  const list = Array.isArray(enabled) ? enabled : ["files", "changes"];
  if (list.includes(child)) return null;
  return [...list, child];
}

export function clickSteps(on, child) {
  if (on) return [{ command: "layout.hide", args: { id: "panel" } }];
  return [
    { command: "layout.select", args: { stack: "panel-body", child } },
    { command: "layout.show", args: { id: "panel" } },
  ];
}
