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
  hard-codes Files/Changes; workbench injects four sibling toggles in
  `tabs-row` and four `panel-body` children (`prs-panel`, `queue-panel`,
  `hosts-panel`, `sessions-panel`).
- Queue and Hosts drill: machines home (`machineCard`, this computer first) →
  machine page (`‹ Machines`). Nav is `shared("queue.machine")` /
  `shared("hosts.machine")` over `_lib/machine_nav.js`.
- Sessions has no home. `sessions/view.js` stacks every machine on one scroll
  and groups rows by first worktree else cwd (`_lib/sessions.js` `groupsOf`).
  No `machine_nav`.
- PRs is a board across repos, not a machine drill.
- Mods have no cross-mod navigation (`mods/AGENTS.md` § Queue). Panel switches
  are the four tab buttons only.

## Known constraints

- `herdr-core serve` loads mod JS at start. A load-backoff fix in tree does
  not apply until that process restarts.
- `prs/workflows.js` 15s batch `status --list` poll is not load-gated.
- Per-repo memory is keyed by shared git-dir path. Relocating the clone
  leaves topic files under the old slug; `dream-scan --memory-dir` of the new
  path is empty until those files are adopted.
