<!-- last-reviewed: 2026-11-19 -->
# Architecture Context

otto-workbench is a dotfiles / developer-environment manager (install, sync,
components). Product install/sync/PATH narrative: `docs/architecture.md`.
This file is agent identity and herdr IA, not that doc.

## Software identity

- Herdr workbench UI is `ai/herdr/app/` (mods + `layouts/workbench.jq`), not
  the homelab Ovis client and not the PR-board web release on the NAS.
- Issue tracker for this repo is GitHub.

## Herdr navigation

- Layout owner: `ai/herdr/app/layouts/workbench.jq`. Herdr's tabs mod
  hard-codes Files/Changes; workbench injects one sibling toggle
  (`workbench-button`) in `tabs-row` and six `panel-body` children
  (`workbench-panel`, `prs-panel`, `queue-panel`, `hosts-panel`,
  `sessions-panel`, `settings-panel`). Hub tiles in `workbench/` select the
  destination children; the sticky toggle stays selected on any of them
  (`_lib/panel_button.js` group helpers).
- Queue, Hosts, and Sessions drill: machines home (`machineCard`, this
  computer first) → machine page (`‹ Machines`). Nav is
  `shared("queue.machine")` / `shared("hosts.machine")` /
  `shared("sessions.machine")` over `_lib/machine_nav.js`.
- Sessions groups rows on a machine page by first worktree else cwd
  (`_lib/sessions.js` `groupsOf`).
- PRs is a board across repos, not a machine drill.
- Mods have no cross-mod navigation (`mods/AGENTS.md` § Queue). Panel switches
  are the hub tiles plus the one tab-bar toggle.

## Known constraints

- `herdr-core serve` loads mod JS at start. A load-backoff fix in tree does
  not apply until that process restarts.
- `prs/workflows.js` 15s batch `status --list` poll is not load-gated.
- Per-repo memory is keyed by shared git-dir path. Relocating the clone
  leaves topic files under the old slug; `dream-scan --memory-dir` of the new
  path is empty until those files are adopted.
