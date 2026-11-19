// Settings stub pane. No exec, no poller.
import { View } from "gpui-kit";
import { renderSettings } from "./view.js";

export default class Settings extends View {
  render(_cx) {
    return renderSettings();
  }
}
