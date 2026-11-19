# The workbench layout: Herdr's shipped main with one Workbench toggle; hub tiles select the other children.
# Each destination is a panel-body child its tile selects. placement tells the board it is the panel
# copy, which offers Full screen; the board tab opened from it carries no placement.
def prs_panel: {id: "prs-panel", type: "block", content: {mod: "prs", props: {placement: "panel"}}};
def queue_panel: {id: "queue-panel", type: "block", content: {mod: "queue"}};
def hosts_panel: {id: "hosts-panel", type: "block", content: {mod: "hosts"}};
def sessions_panel: {id: "sessions-panel", type: "block", content: {mod: "sessions"}};
def workbench_panel: {id: "workbench-panel", type: "block", content: {mod: "workbench"}};
def settings_panel: {id: "settings-panel", type: "block", content: {mod: "settings"}};
# Herdr's tabs mod hard-codes Files/Changes; the workbench toggle is a sibling block
# and those two panels are dropped so the buttons have nothing to select.
# Button width: 2*px 8 + icon 14 + gap 5 + label + pr(SP.sm). 112 for Workbench.
def tabs_row:
  {id: "tabs-row", type: "row", size: .size, children: [
    (.size = {weight: 1}),
    {id: "workbench-button", type: "block", size: {px: 112}, content: {mod: "workbench-button"}}
  ]};

if ([.. | objects | select(.id? == "panel-body")] | length) != 1
then error("main layout has no single panel-body node")
else
  (.. | objects | select(.id? == "panel-body") | .children) |= (map(select(.id != "prs-panel" and .id != "queue-panel" and .id != "hosts-panel" and .id != "sessions-panel" and .id != "workbench-panel" and .id != "settings-panel" and .id != "files" and .id != "changes")) + [workbench_panel, prs_panel, queue_panel, hosts_panel, sessions_panel, settings_panel])
  | (.. | objects | select(.id? == "panel-body")) |= if (.active == "files" or .active == "changes") then .active = "workbench-panel" else . end
  # Full screen hides main and sets the panel to weight 1; Herdr still clamps a node to its max,
  # so main's 900px panel cap would hold the board to 900px of the freed width.
  | (.. | objects | select(.id? == "panel")) |= del(.max)
  # A second run finds tabs inside tabs-row, not under work, so the wrap is a no-op.
  | if ([.. | objects | select(.id? == "tabs")] | length) == 1
    then (.. | objects | select(.id? == "work") | .children) |= map(if .id == "tabs" then tabs_row else . end)
    else error("main layout has no single tabs block")
    end
end
