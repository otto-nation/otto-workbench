# The workbench layout: Herdr's shipped main with a PRs toggle beside the tab bar opening the board as a panel.
# The board is a panel-body child the PRs toggle selects. placement tells the mod it is the panel
# copy, which offers "Open board"; the board tab opened from it carries no placement.
def prs_panel: {id: "prs-panel", type: "block", content: {mod: "prs", props: {placement: "panel"}}};
# Herdr's tabs mod hard-codes Files/Changes, so the PRs toggle is a sibling block.
# prs-button width: 2*px 8 + icon 14 + gap 5 + "PRs" label + pr(SP.sm); 72 leaves slack.
def tabs_row:
  {id: "tabs-row", type: "row", size: .size, children: [
    (.size = {weight: 1}),
    {id: "prs-button", type: "block", size: {px: 72}, content: {mod: "prs-button"}}
  ]};

if ([.. | objects | select(.id? == "panel-body")] | length) != 1
then error("main layout has no single panel-body node")
else
  (.. | objects | select(.id? == "panel-body") | .children) |= (map(select(.id != "prs-panel")) + [prs_panel])
  # A second run finds tabs inside tabs-row, not under work, so the wrap is a no-op.
  | if ([.. | objects | select(.id? == "tabs")] | length) == 1
    then (.. | objects | select(.id? == "work") | .children) |= map(if .id == "tabs" then tabs_row else . end)
    else error("main layout has no single tabs block")
    end
end
