// team-prs entry: the board, or (Task 4) a detail window when props().detail is set.
// A pane copy may have no props — read detail null-safely.
import { props } from "herdr";
import Board from "./board_view.js";

export default class TeamPrs extends Board {
  init(p, cx) {
    this.detail = props()?.detail ?? null;
    // Task 4: Detail.init when this.detail is set; board timers must not run there.
    super.init(p, cx);
  }

  render(cx) {
    // Task 4: Detail.render when this.detail is set.
    return super.render(cx);
  }
}
