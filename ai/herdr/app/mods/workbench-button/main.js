/**
 * The Workbench toggle beside herdr's tab bar. Selected while any workbench child is active.
 */
import { panelButton } from "./_lib/panel_button.js";
import { WORKBENCH_CHILDREN, WORKBENCH_HUB } from "./_lib/workbench.js";

export default panelButton({
  children: WORKBENCH_CHILDREN,
  hub: WORKBENCH_HUB,
  label: "Workbench",
  icon: "mods/workbench-button/workbench.svg",
  buttonId: "panel-workbench",
});
