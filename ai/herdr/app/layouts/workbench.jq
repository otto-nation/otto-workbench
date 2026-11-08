# The workbench layout: Herdr's shipped main with the PR list under the agents list.
def prs: {id: "prs", type: "block", size: {weight: 1}, min: 90,
          content: {mod: "team-prs", props: {view: "sidebar"}}};
if ([.. | objects | select(.id? == "sidebar-lists")] | length) == 1
then (.. | objects | select(.id? == "sidebar-lists") | .children) |= (map(select(.id != "prs")) + [prs])
else error("main layout has no single sidebar-lists node") end
