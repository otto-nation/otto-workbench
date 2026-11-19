/**
 * Hub ids and destination tiles for the Workbench panel.
 * No herdr import — node tests load this module.
 */

export const WORKBENCH_HUB = "workbench-panel";

export const WORKBENCH_CHILDREN = Object.freeze([
  "workbench-panel",
  "prs-panel",
  "queue-panel",
  "hosts-panel",
  "sessions-panel",
  "settings-panel",
]);

export const HUB_TILES = Object.freeze([
  { id: "prs", label: "PRs", caption: "pull requests", child: "prs-panel" },
  { id: "queue", label: "Queue", caption: "otto-queue", child: "queue-panel" },
  { id: "hosts", label: "Hosts", caption: "load, memory, disk", child: "hosts-panel" },
  { id: "sessions", label: "Sessions", caption: "pi sessions", child: "sessions-panel" },
  { id: "settings", label: "Settings", caption: "otto-workbench config", child: "settings-panel" },
]);
