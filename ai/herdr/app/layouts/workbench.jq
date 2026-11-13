# The workbench layout: Herdr's shipped main with PRs and Queue toggles beside the tab bar.
# Each is a panel-body child its toggle selects. placement tells the board it is the panel
# copy, which offers Full screen; the board tab opened from it carries no placement.
def prs_panel: {id: "prs-panel", type: "block", content: {mod: "prs", props: {placement: "panel"}}};
def queue_panel: {id: "queue-panel", type: "block", content: {mod: "queue"}};
# Herdr's tabs mod hard-codes Files/Changes, so the extra toggles are sibling blocks.
# Button width: 2*px 8 + icon 14 + gap 5 + label + pr(SP.sm). 72/88 leave slack.
def tabs_row:
  {id: "tabs-row", type: "row", size: .size, children: [
    (.size = {weight: 1}),
    {id: "prs-button", type: "block", size: {px: 72}, content: {mod: "prs-button"}},
    {id: "queue-button", type: "block", size: {px: 88}, content: {mod: "queue-button"}}
  ]};

if ([.. | objects | select(.id? == "panel-body")] | length) != 1
then error("main layout has no single panel-body node")
else
  (.. | objects | select(.id? == "panel-body") | .children) |= (map(select(.id != "prs-panel" and .id != "queue-panel")) + [prs_panel, queue_panel])
  # Full screen hides main and sets the panel to weight 1; Herdr still clamps a node to its max,
  # so main's 900px panel cap would hold the board to 900px of the freed width.
  | (.. | objects | select(.id? == "panel")) |= del(.max)
  # A second run finds tabs inside tabs-row, not under work, so the wrap is a no-op.
  | if ([.. | objects | select(.id? == "tabs")] | length) == 1
    then (.. | objects | select(.id? == "work") | .children) |= map(if .id == "tabs" then tabs_row else . end)
    else error("main layout has no single tabs block")
    end
end
