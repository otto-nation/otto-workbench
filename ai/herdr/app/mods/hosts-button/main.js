/**
 * The Hosts toggle beside herdr's tab bar. Selects the `hosts-panel` child of `panel-body`.
 */
import { panelButton } from "./_lib/panel_button.js";

export default panelButton({
  child: "hosts-panel",
  label: "Hosts",
  icon: "mods/hosts-button/hosts.svg",
  buttonId: "panel-hosts",
});
