/**
 * The PRs toggle beside herdr's tab bar. Selects the `prs-panel` child of `panel-body`.
 */
import { panelButton } from "./_lib/panel_button.js";

export default panelButton({
  child: "prs-panel",
  label: "PRs",
  icon: "mods/prs-button/pr.svg",
  buttonId: "panel-prs",
});
