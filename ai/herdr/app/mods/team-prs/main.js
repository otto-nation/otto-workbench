import { View, div } from "gpui-kit";
import { stub } from "./_lib/location.js";

export default class TeamPrs extends View {
  render() {
    return div({}, stub ? "team-prs" : "");
  }
}
