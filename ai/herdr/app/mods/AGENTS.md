# herdr-app mods

Conventions for the mods under this directory (`prs`, `prs-button`, `_lib`, `_ui`, …).

## Where things live

- Shared UI components: `_ui/ui.js` (installed into every mod as `./_ui/ui.js`, like `_lib/`). Reuse `button` (outline), `solidButton` (the one primary action), `iconButton`, `chip`, `inset`, `segment`/`segmented`, `filterGroup`, `laneTitle`, `card`, `hoverRow`, `pane`, `paneBar`, `emptyPane`, `scrollBody`, `placeholder`, `caption`, `type`/`text`, `tag`, `statusText`, `sectionLabel`, `ciMark`, `addStat`/`delStat`, `mono` before hand-building a chain; add a component there when a pattern appears a second time
- Every top-level `_*/` directory under `mods/` is a shared tree installed into every mod, so only put real libraries there (no scratch dirs like `_tmp/`)
- Sizes, spacing, fills and breakpoints: `_lib/tokens.js`. Colours come only from `theme()`. No literal pixel or colour values in views
- Pure logic: `_lib/*.js`, covered by `tests/herdr_app_mods_lib_*.bats`
- Handlers use the long-lived context saved in `init` (`this.acx`), never the render's `cx`, for notify and spawn

## The Mine board

- Lanes, in precedence order: Has Conflicts, CI Failing, Has Comments, Changes Requested, Draft, Awaiting Review, Approved, In Queue, Merged (`_lib/columns.js` `mineColumn`)
- Has Comments is feedback `pr comments` would answer, read with `pr batch plan --needs comments` only while Mine shows. It ranks above Changes Requested: owed feedback is the author's work, while a request with every comment answered waits on the reviewer
- A lane's Batch button opens the Batch page with the lane's shown PRs selected and its step set, and starts nothing. `COLUMN_BATCH` in `_lib/batch_launch.js` owns which lanes have one (Has Conflicts and Has Comments with AI on, CI Failing, Draft, Approved with PRs only behind); a lane that waits on someone else has none
- Team, Mine and the Batch page share one `prs.filters` (Repo, Label, Drafts, Author on Team only). Drafts is one setting for Team and one for Mine and Batch; add a filter in `_lib/columns.js` so the board and the form share its matcher

## Full screen

- Only the panel copy (mounted with `props.placement === "panel"`) offers it. The layout owns "is full screen": `main` hidden (`_lib/fullscreen.js` `fullscreenState` reads it from `layout()`; a shown node's `hidden` is absent, not `false`). Enter hides `main` and sets the panel to weight 1; exit shows `main` and restores the width saved in `shared("prs.panelPx")`. `_lib/fullscreen_run.js` owns that sequence; the board button and `prs-button` both call it
- A render is not re-run by a layout click, but `layout()` read in a `cx.timer.every` callback is current, so anything derived from it is refreshed by a timer, not left to `render`
- herdr's tabs mod shows or hides the panel per space, so a space switch (or the Files button) can leave `main` and the panel both hidden. `prs-button` heals that from a one-second timer (`healDue` is the rule) by exiting full screen, exits at startup so the app never opens with the terminals hidden, and its toggle exits before it hides the panel
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

## Checking and installing

- Check an assembled copy, not the source tree: `_herdr_app_install_mods "$tmp"` then `herdr-app check "$tmp/mods/prs"` (source `lib/ui.sh`, `lib/constants.sh` and `ai/herdr/steps.sh` first)
- Install live with `rm -rf ~/.config/herdr-app/mods/prs` then `_herdr_app_install_mods ~/.config/herdr-app`. Never patch the live files; edit the source and reinstall
- A reload briefly reports "no main.js"; `herdr-app status` settles to `"errors": []`

## Screenshots

- `herdr-app shot` races: select the tab (`herdr-app do tab.select --id <id>`), wait 8–10 s, shoot twice, then re-select the user's tab. Repeat if the shot shows another session
- Overlays do not appear in shots
