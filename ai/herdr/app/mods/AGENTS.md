# herdr-app mods

Conventions for the mods under this directory (`prs`, `prs-button`, `queue`, `queue-button`, `_lib`, `_ui`, …).

## Where things live

- Shared UI components: `_ui/ui.js` (installed into every mod as `./_ui/ui.js`, like `_lib/`). Reuse `button` (outline), `solidButton` (the one primary action), `iconButton`, `ghostButton`, `checkbox`, `checkRow`, `popoverSurface`, `inset`, `segment`/`segmented`, `laneTitle`, `card`, `hoverRow`, `pane`, `paneBar`, `emptyPane`, `scrollBody`, `placeholder`, `caption`, `type`/`text`, `tag`, `statusText`, `sectionLabel`, `badge`/`badgeRow`/`statsRow`, `addStat`/`delStat`, `mono` before hand-building a chain; add a component there when a pattern appears a second time
- Every top-level `_*/` directory under `mods/` is a shared tree installed into every mod, so only put real libraries there (no scratch dirs like `_tmp/`)
- Sizes, spacing, fills and breakpoints: `_lib/tokens.js`. Colours come only from `theme()`. No literal pixel or colour values in views
- Pure logic: `_lib/*.js`, covered by `tests/herdr_app_mods_lib_*.bats`
- Handlers use the long-lived context saved in `init` (`this.acx`), never the render's `cx`, for notify and spawn

## Board code lives in _lib

- Pull composition (`_lib/board_store.js` `createBoardStore` over `_lib/board_pull.js` `pullTarget`), lanes, facets, chips, counts, lane status and lane Batch presets (`_lib/board_model.js` `boardModel`), and badges (`_lib/pr_status.js` `presentationOf`) live in `_lib`. The herdr-app mod and the web board only choose targets, say what they show (`want`, from `wantFor`), schedule and draw. A board feature lands in `_lib` or in neither view; `tests/herdr_board_parity.bats` enforces it. Python owns facts, lanes, badges and columns (`pr board`); the views add only the live review terminal and the batch item. An older `pr` on a machine reads as `outdated`, never a partial board. Review-run identity and open/load decisions are `_lib/history.js` (`runKey`, `toggleOpen`); the live run's `stamp` is `""`.
- Has Comments (owed) loads on demand: only while Mine is in view (`LANE_NEEDS` / `wantFor(tab)`; the mod's panel copy while the panel shows the board, the web while a connected page is on Mine). Until owed has loaded, the lane shows a static `…` and "Loading comments…", never an empty lane that reads as no comments; a failed read shows `×` and the reason. It ranks above Changes Requested: owed feedback is the author's work, while a request with every comment answered waits on the reviewer

## The Mine board

- Lanes, in precedence order: Merged, In Queue, In Review (a review running now), Has Conflicts, CI Failing, Has Comments, Changes Requested, Approved, Awaiting Review (`board/columns.py`, read through `_lib/columns.js` `laneOf`)
- Card problems and every PR slot come from `_lib/pr_status.js` `presentationOf`, shared by prs/board.js, prs/compact.js, prs/detail.js and the web; state tags sit top right in the title row; positive state shows only in the detail; batch run/step/item status kinds and step labels come from `_lib/batch.js` (`RUN_STATUS`, `STEP_STATUS`, `STEP_LABEL`, `AI_STEPS`), never a view map
- A lane's Batch button opens the Batch page with the lane's shown PRs selected and its step set, and starts nothing. `COLUMN_BATCH` in `_lib/batch_launch.js` owns which lanes have one (Has Conflicts and Has Comments with AI on, CI Failing, Approved with PRs only behind); a lane that waits on someone else has none
- Team, Mine and the Batch page share one `prs.filters`. `_lib/filters.js` owns `filterAction`/`facetView`/`barModel` (shared with the web); `prs/filter_bar.js` and `web/public/filter_bar.js` only draw. Each value is included or hidden; exclude wins; Author and Hide bots are Team only; a stale include is ignored; a stale exclude is kept as a tag. Author rows carry a per-author count in `option.count` (never in the label) and bots sit in their own `barModel` group. Drafts is one setting for Team and one for Mine and Batch; a draft sits in the lane for its real state with a Draft tag, never in a lane of its own, and keeps its real CI status

## Trying models

- A `Models` popover (`prs/model_picker.js`) sits in the PR detail's action bar and in the Batch form's Options. Choices come from `pr models --json` as an inline searchable list per phase (Configured, Tiers, Enabled in Pi, then by provider); blank / Configured uses the configured model. Free text stays as Custom… and as the fallback when `pr` is older, times out, or returns bad JSON. The set values show as text beside the button. Overrides are per run and session-only: `_lib/model_overrides.js` turns them into `pr --phase-model PHASE=MODEL`, which `pr` validates (unknown phase, bad model, a review phase on a light or Haiku model), so a bad value comes back as that run's own error. Saved models are `otto-workbench config model`, not this UI
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
- herdr's tabs mod shows or hides the panel per space, so a space switch (or the Files button) can leave `main` and the panel both hidden. Each tab-bar toggle (`_lib/panel_button.js`) heals that from a one-second timer (`healDue` is the rule) by exiting full screen, exits at startup so the app never opens with the terminals hidden, and its click exits before it hides the panel. `ai/herdr/app/layouts/workbench.jq` mounts `prs-panel` and `queue-panel` as `panel-body` children and `prs-button` / `queue-button` in `tabs-row`; each toggle selects its child with `layout.select`
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
- `tests/herdr_app_mods_style.bats` holds the views to the brand rules a grep
  can see: no hover that changes a border, no `pill`, no ✗ glyph.

Where `DESIGN.md` is silent, keep to those owners rather than inventing a value,
and record the gap in otto-nation/otto-nation.github.io's issue tracker.

## Loading

- Owners: `_lib/scope.js` `repoStates`, `_lib/load_state.js` `errorsStatus`, `prs/load_status.js`
- Indicators are static glyphs (queued `·`, loading/hydrating `…`, stale `!`, failed `×`): no spin, frames, loop, or CSS animation
- A lane whose data is read on demand carries `status`/`message` from `_lib/board_model.js` (`ready`, `loading`, `error`): until owed has loaded for every shown repo, Has Comments shows `…` in its count slot and "Loading comments…" in an empty body, never "None"; a failed owed read shows `×` and the reason. The lane stays shown while not ready and offers no Batch until it is. Views draw it (`_ui/ui.js` `laneTitle`/`sectionLabel`, `web/public/dom.js` `laneTitle`) and decide nothing
- The header line, progress, and strip appear only after 1s (`LOAD_DELAY_MS`); before that, empty boards stay muted "Loading…"
- `hydrating` and `hydrated: false` come from `pr board`'s listing phase (CI loading in a row's presentation) until its status phase lands

## Checking and installing

- Check an assembled copy, not the source tree: `_herdr_app_install_mods "$tmp"` then `herdr-app check "$tmp/mods/prs"` (source `lib/ui.sh`, `lib/constants.sh` and `ai/herdr/steps.sh` first)
- Install live with `rm -rf ~/.config/herdr-app/mods/prs` then `_herdr_app_install_mods ~/.config/herdr-app`. Never patch the live files; edit the source and reinstall
- A reload briefly reports "no main.js"; `herdr-app status` settles to `"errors": []`

## Screenshots

- `herdr-app shot` races: select the tab (`herdr-app do tab.select --id <id>`), wait 8–10 s, shoot twice, then re-select the user's tab. Repeat if the shot shows another session
- Overlays do not appear in shots
