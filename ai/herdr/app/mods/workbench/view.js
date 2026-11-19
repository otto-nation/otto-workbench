// Workbench hub: destination tiles with static captions. No counts, no refresh.
import { div } from "gpui-kit";
import { theme } from "herdr";
import { HUB_TILES } from "./_lib/workbench.js";
import { SP } from "./_lib/tokens.js";
import { pane, paneBar, scrollBody, card, text, type, caption } from "./_ui/ui.js";

export function renderWorkbench(props) {
  const t = theme();
  return pane("workbench")
    .child(paneBar().child(text("title", "Workbench")))
    .child(scrollBody().p(SP.md).gap(SP.md)
      .children(HUB_TILES.map((tile) =>
        card(`workbench-tile-${tile.id}`)
          .gap(SP.xs)
          .on_click(() => props.onOpen(tile.child))
          .child(type(div().min_w(0).truncate(), "title").text_color(t.foreground).child(tile.label))
          .child(caption(tile.caption)))));
}
