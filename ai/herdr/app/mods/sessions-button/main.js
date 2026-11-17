/**
 * The Sessions toggle beside herdr's tab bar. Selects the `sessions-panel` child of `panel-body`.
 */
import { panelButton } from "./_lib/panel_button.js";

export default panelButton({
  child: "sessions-panel",
  label: "Sessions",
  icon: "mods/sessions-button/sessions.svg",
  buttonId: "panel-sessions",
});
