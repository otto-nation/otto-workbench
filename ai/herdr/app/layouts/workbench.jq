# The workbench layout: Herdr's shipped main with the PR list under the agents list.
def prs: {id: "prs", type: "block", size: {weight: 1}, min: 90,
          content: {mod: "team-prs", props: {view: "sidebar"}}};
# The board is a panel-body child the PRs toggle selects.
def prs_panel: {id: "prs-panel", type: "block", content: {mod: "team-prs"}};
# Herdr's tabs mod hard-codes Files/Changes, so the PRs toggle is a sibling block.
def tabs_row:
  {id: "tabs-row", type: "row", size: .size, children: [
    (.size = {weight: 1}),
    {id: "prs-button", type: "block", size: {px: 64}, content: {mod: "prs-button"}}
  ]};

if ([.. | objects | select(.id? == "sidebar-lists")] | length) != 1
then error("main layout has no single sidebar-lists node")
else
  (.. | objects | select(.id? == "sidebar-lists") | .children) |= (map(select(.id != "prs")) + [prs])
  | if ([.. | objects | select(.id? == "panel-body")] | length) != 1
    then error("main layout has no single panel-body node")
    else
      (.. | objects | select(.id? == "panel-body") | .children) |= (map(select(.id != "prs-panel")) + [prs_panel])
      # A second run finds tabs inside tabs-row, not under work, so the wrap is a no-op.
      | if ([.. | objects | select(.id? == "tabs")] | length) == 1
        then (.. | objects | select(.id? == "work") | .children) |= map(if .id == "tabs" then tabs_row else . end)
        else error("main layout has no single tabs block")
        end
    end
end
