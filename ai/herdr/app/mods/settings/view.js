// Settings stub: title only. Models UI lives in a companion spec.
import { pane, paneBar, text, placeholder } from "./_ui/ui.js";

export function renderSettings() {
  return pane("settings")
    .child(paneBar().child(text("title", "Settings")))
    .child(placeholder("Settings"));
}
