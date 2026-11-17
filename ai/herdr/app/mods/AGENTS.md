# herdr-app mods

Conventions for the mods under this directory (`prs`, `prs-button`, `queue`, `queue-button`, `space-prs`, `hosts`, `hosts-button`, `_lib`, `_ui`, …).

## Where things live

- Shared UI components: `_ui/ui.js` (installed into every mod as `./_ui/ui.js`, like `_lib/`). Reuse `button` (outline), `solidButton` (the one primary action), `dangerButton` (button-danger: destructive confirm only), `iconButton`, `menuButton` (menu-button; the value goes beside it), `backButton` (back: first, then the title at `SP.xs`), `checkbox`, `checkRow`, `popoverSurface`, `inset` (static bordered box), `scrollInset` (bordered outer, inner scroller — never a self-scrolling TextView), `segment`/`segmented`, `laneTitle`, `card`, `hoverRow`, `pane`, `paneBar`, `actionBar` (pinned footer, the mirror of `pinned`), `emptyPane`, `scrollBody`, `placeholder`, `caption`, `meter` (drawn capacity track from `meterModel`, colour from `theme()`), `type`/`text`, `tag`, `statusText`, `sectionLabel`, `badge`/`badgeRow`/`statsRow`, `addStat`/`delStat`, `textInput` (the one text field: body type, outline border, control height; never a bare `Input.new`), `keyValue` (compact two-column facts: section-muted keys, meta values, code for paths), `mono` before hand-building a chain; add a component there when a pattern appears a second time
- Every top-level `_*/` directory under `mods/` is a shared tree installed into every mod, so only put real libraries there (no scratch dirs like `_tmp/`)
- Sizes, spacing, fills and breakpoints: `_lib/tokens.js`. Colours come only from `theme()`. No literal pixel or colour values in views
- Pure logic: `_lib/*.js`, covered by `tests/herdr_app_mods_lib_*.bats`
- Sidebar Space PRs: `space-prs/`, mounted after herdr's `sidebar-lists` children in the workbench layout
- Handlers use the long-lived context saved in `init` (`this.acx`), never the render's `cx`, for notify and spawn

## Board code lives in _lib

- Pull composition (`_lib/board_store.js` `createBoardStore` over `_lib/board_pull.js` `pullTarget`), lanes, facets, chips, counts, lane status and lane Batch presets (`_lib/board_model.js` `boardModel`), and badges (`_lib/pr_status.js` `presentationOf`) live in `_lib`. The herdr-app mod and the web board only choose targets, say what they show (`want`, from `wantFor`), schedule and draw. `space-prs` is a third view: it only reads the store offline plus `_lib/space_prs.js`, and is parity-scanned with the other two. A board feature lands in `_lib` or in neither view; `tests/herdr_board_parity.bats` enforces it. Python owns facts, lanes, badges and columns (`pr board`); the views add only the live review terminal and the batch item. A `queue` ticket on a row (`queued` / `running`) is a fact; `_lib/columns.js` `laneOf` and `presentationOf` read it only when that machine's `pr board` features include `queue-lanes`. An older `pr` on a machine reads as `outdated`, never a partial board. Review-run identity and open/load decisions are `_lib/history.js` (`runKey`, `toggleOpen`); the live run's `stamp` is `""`.
- Has Comments (owed) loads on demand: only while Mine is in view (`LANE_NEEDS` / `wantFor(tab)`; the mod's panel copy while the panel shows the board, the web while a connected page is on Mine). Until owed has loaded, the lane shows a static `…` and "Loading comments…", never an empty lane that reads as no comments; a failed read shows `×` and the reason. It ranks above Changes Requested: owed feedback is the author's work, while a request with every comment answered waits on the reviewer

## The Mine board

- A PR header's PR ref, branch, short commit and Copy link copy on click and read "Copied" for `COPIED_MS`: what is copyable and the https-only PR URL come from `_lib/pr_link.js` (`copyFields`, `prUrl`), drawn by `_ui/ui.js` `copyable` here and `public/dom.js` `copyable` on the web, and written to the clipboard from `prs/main.js` `copyField` only.
- Lanes, in precedence order: Merged, In Queue, In Review (a review running now, including a running review ticket), Has Conflicts, CI Failing, Has Comments, Changes Requested, Approved, Awaiting Review (`board/columns.py`, read through `_lib/columns.js` `laneOf`). A queued review stays in its resting lane with a `queued` status; Team puts it in In Queue and a running one in In Progress
- Card problems and every PR slot come from `_lib/pr_status.js` `presentationOf`, shared by prs/board.js, prs/compact.js, prs/detail.js and the web; state tags sit top right in the title row; positive state shows only in the detail; batch run/step/item status kinds and step labels come from `_lib/batch.js` (`RUN_STATUS`, `STEP_STATUS`, `STEP_LABEL`, `AI_STEPS`), never a view map
- Mine's Select for batch (header) shows a box on each open PR and a lane Select all, with a selection bar (Clear, Cancel, Continue); Continue opens the Batch page with them selected and starts nothing. When the selection is one lane's, that lane's step set carries over: `COLUMN_BATCH` in `_lib/batch_launch.js` owns which lanes have one (Has Conflicts and Has Comments with AI on, CI Failing, Approved with PRs only behind), `selectionPreset` picks it; a lane that waits on someone else has none. There is no Batch button on a lane
- The detail of the viewer's own open PR offers a fix per present problem, from the row's `issues` kinds (`_lib/pr_fixes.js`): Rebase (conflicts), Fix CI (CI failing), Answer comments (owed comments or changes requested), each a one-PR `pr batch run` step with AI on that `BatchLauncher.fixOne` starts at once, without touching the Batch page's form. It follows in the Batch tab. Own PRs are not offered Approve or Request changes (`actionsFor` `own`)
- A row's `lock` (Python `board.locks`) badges in the busy slot and disables every `pr`-starting control through `_lib/actions.js` `blockReason` / `_lib/batch_launch.js` `batchReason`; mod views never decide this themselves
- An expanded recent run on the Batch page offers Open trail when that run has a trail id, which launches `otto-log show` on the run's machine
- An expanded recent run and a PR's Needs-you card offer Open session: the run's one pi session (`pr batch session RUN_ID`, which owns its argv, env and checkout). `_lib/run_session.js` `openRunSession` asks `pr batch session RUN_ID --json` on the run's machine, brings forward the live hc terminal labelled `otto.session=<session_id>` (its space, tab and pane, or `terminal.keep`), and otherwise starts `pr batch session RUN_ID` through `hc run --bg` and keeps it as a tab. herdr hears of a terminal a moment after `hc run` replies and refuses `terminal.keep` until then, so every keep of a just-started terminal (Open session, Open trail) goes through `_lib/launch.js` `keepStarted`, which waits up to `KEEP_WAIT_MS` for `terminals()` to list it and otherwise reports the address to attach to. Beside it, as text: "waiting on you: N PRs" from the report's `sessions`. It needs `batch-session` in that machine's `pr board` `features`; otherwise it says `Update pr on this machine to open sessions (otto-workbench sync)`. A PR's detail header offers the same Open session for its target's session (`pr --branch B target session --json`, started as `pr target session` in the branch's worktree), gated on `target-session`; a target session with no labelled terminal brings forward an agent pane already in that checkout (an `otto.worktree` label on its terminal counts over its cwd) before starting one; batch sessions never adopt; `_lib/run_session.js` `SESSION_KINDS` is the one table of what differs
- Team, Mine and the Batch page share one `prs.filters`. `_lib/filters.js` owns `filterAction`/`facetView`/`barModel` (shared with the web); `prs/filter_bar.js` and `web/public/filter_bar.js` only draw. Each value is included or hidden; exclude wins; Author and Hide bots are Team only; a stale include is ignored; a stale exclude is kept as a tag. Author rows carry a per-author count in `option.count` (never in the label) and bots sit in their own `barModel` group. Drafts is one setting for Team and one for Mine and Batch; a draft sits in the lane for its real state with a Draft tag, never in a lane of its own, and keeps its real CI status

## Space PRs

- Mounted by `ai/herdr/app/layouts/workbench.jq` after herdr's `sidebar-lists` children when herdr ships one. One group per repo among the open spaces, named after the first space on it; rows are that repo's open PRs whose branch has a worktree (`worktree` on the `pr board` row, a Python fact read at read time, gated on the `worktrees` feature: an older `pr` reads as an outdated group with `WORKTREES_OUTDATED_TEXT`). The block draws one pinned `paneHeader("Space PRs")` (rail: a compact pinned "PRs" label) with a refresh button whose spinner uses `refreshSpinning` like Hosts, then one scroller. A wide row is a linked `card` (hairline, card radius and padding, `SP.xs` apart) on herdr's `chrome` sidebar fill: the branch with `#N` as plain code text on the right, then problem text from `presentationOf` (positive state hidden), `+N` when more open PRs share the branch (lowest number shown) and, muted, the owning agent's tab name. Clicking anywhere on the card opens its target, or, for a row with no PR, selects the space. A rail (compact width) row is a `hoverRow` with the same click and shows only `#N`; rows with no PR are not drawn in rail. Group labels scroll with their rows and still select the space. Loading is `…`, a failed repo `×` plus the reason; a ready group with no rows is dropped; an empty list is the inset placeholder "No PR worktrees in these spaces"
- The owner is `_lib/run_session.js` `agentPaneFor`: an agent whose terminal is labelled `otto.worktree` with the worktree (`LABEL_WORKTREE`, written by `ai/lib/pr/agent_worktree.py` through `ai/bin/report-agent-worktree` from both harnesses), then an agent whose cwd is in it. The card (or rail `hoverRow`) focuses that agent, else a shell pane in the worktree (`shells: true`), else opens the PR's target session from the worktree
- Listing-only pulls; a repo refreshes only when its cache is stale (`_lib/board_store.js` `refreshAfterMs` over `repos[].cached_at`, gated on `cache-age`). Groups from `_lib/space_prs.js` `spaceWorktreePrs`

## The Queue panel

- Lanes, position, timing, grouping and staleness live in `_lib/queue_lanes.js` (`lanesModel`, `staleness`, `refreshSpinning`). The machines home and pages (`homeModel`, `machinePage`, `meterModel`) live in `_lib/queue_home.js`. Navigation (`selectMachine`, `clearMachine`, `pageOf`) lives in `_lib/machine_nav.js`. `queue/main.js` chooses machines and asks the model; `queue/view.js` only draws
- Default screen is the machines home: one `card` per machine from `homeModel` — name plus a staleness caption, a `meter` per class, the counts line (`N failed today` from the CLI's `failed24h` when present, else the rows, `20+` at `RECENT_TERMINAL`). When the host is not admitting heavy work, `hostNote` is `heavy waits: <reason>` (the CLI's reason string) and the card and machine header draw it as one `statusText('warn')` line. The card is one click target that opens that machine via `selectMachine` into `shared("queue.machine")`. No runs on the home. A disappeared machine falls back through `pageOf` to the home. The machine page starts with an outline Back (`‹ Machines`), then meters, counts, the host note when present, and Running / Waiting / Finished: a non-empty section is a `card` with `laneTitle`; an empty one is one muted header line (`Waiting 0`), no `None`. Machine card, header, meter rows and pane header live in `_ui/ui.js`, shared with Hosts
- Hosts is read-only; a home of one `machineCard` per machine (this one first, then `_lib/machines.js` `remoteOns`) and, on click, a machine page (`machineHeader`, a Processes card with a CPU/Memory toggle, a Details card) with the nav state `shared("hosts.machine")` over `_lib/machine_nav.js`; the model is `_lib/hosts.js` (`severityOf`, `hostSummary`, `machineModel`, `hostsModel`, `hostPage`, `topProcesses`), loaded through `_lib/hosts_load.js` `loadHealth`; the card, header, meter rows and pane header are the same `_ui/ui.js` pieces Queue uses (`machineCard`, `machineHeader`, `meterRows`, `paneHeader`); status `ok`/`loaded`/`strained` from the worst of load per core (`BUSY_PER_CORE`, `STRAINED_PER_CORE` in `_lib/load_backoff.js`), memory free and disk used; an unreachable or unsynced host is `warn`, never `ok`; polls every 15 s only while the panel is selected; nothing here kills or changes a process
- A row is glyph, action + subject (or label), timing, then `#id · class · position · retry/failure`. A coalesced group expands inline against its model `key` in `shared("queue.groups")`. An expanded row offers `Open PR` when `row.subject` is set (copies the GitHub URL; mods have no cross-mod navigation) and `Open terminal` when `row.terminal` is set (`focusSteps`). There is no All / Live / Failed filter. The refresh icon spins only when `refreshSpinning` says so

## Trying models

- A `Models` popover (`prs/model_picker.js`) sits in the PR detail's action bar and in the Batch form's Options. Choices come from `pr models --json` as an inline searchable list per phase (Configured, Tiers, Enabled in Pi, then by provider); blank / Configured uses the configured model. An expanded phase offers Use for all review phases, which copies that value onto every review phase this picker shows, skipping a phase whose row would be disabled. Free text stays as Custom… and as the fallback when `pr` is older, times out, or returns bad JSON. The set values show as text beside the button. Overrides are per run and session-only: `_lib/model_overrides.js` turns them into `pr --phase-model PHASE=MODEL`, which `pr` validates (unknown phase, bad model, a review phase on a light or Haiku model), so a bad value comes back as that run's own error. Saved models are `otto-workbench config model`, not this UI
- `REVIEW_PHASES` / `BATCH_PHASES` must name phases `pr` knows; `tests/herdr_app_mods_lib_model_overrides.bats` checks them against the registry. Only Review, Re-run, Recover and Batch carry the flags: posting and dismissing run no agent
- A batch resumed with `pr batch resume` does not replay its overrides

## Posting

- Post actions create a draft or publish: Post draft, Post comment, Approve, Request changes, Publish draft. Same-label pairs are never enabled together; the bar draws only enabled actions
- A pending draft shows "draft on GitHub — only you can see it" and offers Publish draft / Approve / Request changes
- Posting needs `review-publish` in that machine's `pr board` `features`; otherwise the buttons say to update `pr` (`Update pr on this machine to post (otto-workbench sync)`)
- Owners: `_lib/actions.js` `POSTS`, `_lib/history.js` `postOf`

## Full screen

- Only the panel copy (mounted with `props.placement === "panel"`) offers it. The layout owns "is full screen": `main` hidden (`_lib/fullscreen.js` `fullscreenState` reads it from `layout()`; a shown node's `hidden` is absent, not `false`). Enter hides `main` and sets the panel to weight 1; exit shows `main` and restores the width saved in `shared("prs.panelPx")`. `_lib/fullscreen_run.js` owns that sequence; the board button and each tab-bar toggle (`_lib/panel_button.js`) call it
- Herdr clamps a node to its `max` even at weight 1, so `ai/herdr/app/layouts/workbench.jq` drops the panel's `max` (900 in Herdr's main layout); with it, full screen holds the board to 900px and leaves the rest of the area empty
- A render is not re-run by a layout click, but `layout()` read in a `cx.timer.every` callback is current, so anything derived from it is refreshed by a timer, not left to `render`
- herdr's tabs mod shows or hides the panel per space, so a space switch (or the Files button) can leave `main` and the panel both hidden. Each tab-bar toggle (`_lib/panel_button.js`) heals that from a one-second timer (`healDue` is the rule) by exiting full screen, exits at startup so the app never opens with the terminals hidden, and its click exits before it hides the panel. `ai/herdr/app/layouts/workbench.jq` mounts `prs-panel`, `queue-panel` and `hosts-panel` as `panel-body` children and `prs-button` / `queue-button` / `hosts-button` in `tabs-row`; each toggle selects its child with `layout.select`
- No Esc to exit: a mod only gets keys when it is focusable

## Design

Read the brand's `DESIGN.md` before changing a view (root `AGENTS.md` § UI and
styling says where), and apply its Do's and Don'ts, Elevation, Shapes and
Components. Each value has one owner, so this section names the owners and
restates none of their contents:

- Colour: `theme()`, which `ai/herdr/app/palettes/otto.toml` fills from the
  brand tokens. To find the field for a brand role, read the palette's
  `[themes.*.colors]` and `ansi` entries for both schemes. Text takes a field
  the palette maps to ink or ink-muted in both, never one mapped to outline,
  which the brand defines as a non-text boundary. Prefer a named field to an
  `ansiN` slot when one carries the role in both schemes.
- Status glyphs and colour fields: `_lib/status.js`
- Size, spacing, radius and type: `_lib/tokens.js`, which reads the brand's
  values from `_lib/brand.generated.js` (rendered by
  `bin/local/generate-herdr-app-themes`). Text takes one of `TYPE`'s roles,
  applied with `_ui/ui.js` `type()` or `text()`, never a bare size.
- Shape of a control, card, tag, status or label: `_ui/ui.js`, one function per
  `DESIGN.md` component.
- There is no ghost button: a labelled action is an outline `button`, the primary one a `solidButton`, an icon-only one an `iconButton`; a borderless clickable reads as text.
- Buttons follow `DESIGN.md` **button hierarchy**, **confirm**,
  **menu-button**, **back**, **action-bar**, **icon-button**, the `disabled`
  state and the Layout click-target paragraph.
- `tests/herdr_app_mods_style.bats` holds the views to the brand rules a grep
  can see: no hover that changes a border, no `pill`, no ✗ glyph.

Where `DESIGN.md` is silent, keep to those owners rather than inventing a value,
and fix the gap in otto-nation/otto-nation.github.io's `packages/brand/DESIGN.md` directly.

## Loading

- Owners: `_lib/scope.js` `repoStates`, `_lib/load_state.js` `errorsStatus`, `prs/load_status.js`
- Indicators are static glyphs (queued `·`, loading/hydrating `…`, stale `!`, failed `×`): no spin, frames, loop, or CSS animation
- A lane whose data is read on demand carries `status`/`message` from `_lib/board_model.js` (`ready`, `loading`, `error`): until owed has loaded for every shown repo, Has Comments shows `…` in its count slot and "Loading comments…" in an empty body, never "None"; a failed owed read shows `×` and the reason. The lane stays shown while not ready and offers no Batch until it is. Views draw it (`_ui/ui.js` `laneTitle`/`sectionLabel`, `web/public/dom.js` `laneTitle`) and decide nothing
- The header line, progress, and strip appear only after 1s (`LOAD_DELAY_MS`); before that, empty boards stay muted "Loading…"
- `hydrating` and `hydrated: false` come from `pr board`'s listing phase (CI loading in a row's presentation) until its status phase lands

## Run output

- Owner: `_lib/run_output.js` `outputModel` (status kinds from `_lib/status.js`, elapsed via `durationText`, last meaningful line, landed run). The panel auto-opens on a successful launch. Glyphs are static (no spinners). `prs/detail.js` draws one `paneBar`: a status row with the outline cluster (Show/Hide output only when there is output, Open as tab, Hide, View review for a finished review/rerun/recover), one summary line from `line`/`lineKind`, and the tail in `scrollInset` only while open. A finished review/rerun/recover swaps to the landed run (`reviewed_at >= startedMs`); the raw tail stays behind Show output. The running batch step shows the same one-line summary, from a 20-line preview on the existing 3 s timer.

## Checking and installing

- Check an assembled copy, not the source tree: `_herdr_app_install_mods "$tmp"` then `herdr-app check "$tmp/mods/prs"` (source `lib/ui.sh`, `lib/constants.sh` and `ai/herdr/steps.sh` first)
- Install live with `rm -rf ~/.config/herdr-app/mods/prs` then `_herdr_app_install_mods ~/.config/herdr-app`. Never patch the live files; edit the source and reinstall
- A reload briefly reports "no main.js"; `herdr-app status` settles to `"errors": []`

## Screenshots

- `herdr-app shot` races: select the tab (`herdr-app do tab.select --id <id>`), wait 8–10 s, shoot twice, then re-select the user's tab. Repeat if the shot shows another session
- Overlays do not appear in shots
