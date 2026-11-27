<!-- last-reviewed: 2026-11-27 -->
# Architecture Context

otto-workbench is a dotfiles / developer-environment manager (install, sync,
components). Product install/sync/PATH narrative: `docs/architecture.md`.
This file is agent identity and herdr IA, not that doc.

## Software identity

- Herdr workbench UI is `ai/herdr/app/` (mods + `layouts/workbench.jq`), not
  the homelab Ovis client and not the PR-board web release on the NAS.
- Issue tracker for this repo is GitHub.
- This repo has no Taskfile and does not use go-task. GitHub
  PATs for `pr` commands live in `$WORKBENCH_CONFIG_DIR/github.env`
  (or `<repo>/.workbench/github.env` per repo), not
  `~/.config/task/taskfile.env` or `.taskfile/taskfile.env`.
  <!-- added by /architecture 2026-11-27 -->

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
- Sessions lists Pi and Claude Code sessions per machine, not Pi-only.
  Claude rows have no TL;DR, PR tags, or Rename.
  <!-- added by /architecture 2026-11-27 -->
- PRs is a board across repos, not a machine drill.
- PRs: Team is other people's PRs, Mine is the viewer's; the same PR is
  never on both.
  <!-- added by /architecture 2026-11-27 -->
- Settings edits this machine's model config. Picker writes mark the
  pane dirty; `Sync` (`otto-workbench ai sync`) applies them.
  <!-- added by /architecture 2026-11-27 -->
- Mods have no cross-mod navigation (`mods/AGENTS.md` § Queue). Panel switches
  are the hub tiles plus the one tab-bar toggle.

## Known constraints

- `herdr-core serve` loads mod JS at start. A load-backoff fix in tree does
  not apply until that process restarts.
- `prs/workflows.js` 15s batch `status --list` poll is not load-gated.
- Per-repo memory is keyed by hosted origin when the clone has one, else by
  shared git-dir path. Relocating a clone with `remote.origin.url` keeps the
  same directory; a leftover path-keyed slug is adopted by
  `ai/claude/migrations/20261125-memory-key-origin.sh`.
