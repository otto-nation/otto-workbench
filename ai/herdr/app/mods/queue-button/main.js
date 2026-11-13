/**
 * The Queue toggle beside herdr's tab bar. Selects the `queue-panel` child of `panel-body`.
 */
import { panelButton } from "./_lib/panel_button.js";

export default panelButton({
  child: "queue-panel",
  label: "Queue",
  icon: "mods/queue-button/queue.svg",
  buttonId: "panel-queue",
});
