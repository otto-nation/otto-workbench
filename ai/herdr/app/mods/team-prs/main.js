// team-prs entry: the board, or a detail window when props().detail is set. A pane copy may have
// no props, so detail is read null-safely. The window copy never starts the board's timers.
import { exec, props, closeOverlay } from "herdr";
import Board from "./board_view.js";
import { renderDetail } from "./detail.js";

export default class TeamPrs extends Board {
  init(p, cx) {
    this.detail = props()?.detail ?? null;
    if (this.detail) return;
    super.init(p, cx);
  }

  render(cx) {
    if (!this.detail) return super.render(cx);
    return renderDetail(this.detail, {
      nowMs: Date.now(),
      onClose: () => closeOverlay(props().overlay),
      // The browser is on this Mac even when the PR's repo is on a remote machine.
      onOpenUrl: (url) => exec("open", [url]),
    });
  }
}
