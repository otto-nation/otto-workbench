// Workbench hub pane. Tiles select a panel-body child; no exec, no poller.
import { View } from "gpui-kit";
import { run } from "herdr";
import { renderWorkbench } from "./view.js";

export default class Workbench extends View {
  render(_cx) {
    return renderWorkbench({
      onOpen: (child) => run("layout.select", { stack: "panel-body", child }),
    });
  }
}
