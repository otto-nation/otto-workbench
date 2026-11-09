# herdr-app mods

Conventions for the mods under this directory (`team-prs` — the Batch button in the board header names the machine's latest `pr batch` run and opens the list of runs; a PR card in the run shows its batch state (a failed step, needs you, running, done); a PR's detail has a Batch tab with its notes, steps and each step's log, `_lib`, …).

## Where things live

- Shared UI components: `_ui/ui.js` (installed into every mod as `./_ui/ui.js`, like `_lib/`). Reuse `button`, `chip`, `pill`, `inset`, `segment`/`segmented`, `filterGroup`, `eyebrow`, `card`, `hoverRow`, `pane`, `paneBar`, `emptyPane`, `scrollBody`, `placeholder`, `caption`, `ciMark`, `addStat`/`delStat`, `mono` before hand-building a chain; add a component there when a pattern appears a second time
- Every top-level `_*/` directory under `mods/` is a shared tree installed into every mod, so only put real libraries there (no scratch dirs like `_tmp/`)
- Sizes, spacing, fills and breakpoints: `_lib/tokens.js`. Colours come only from `theme()`. No literal pixel or colour values in views
- Pure logic: `_lib/*.js`, covered by `tests/herdr_app_mods_lib_*.bats`
- Handlers use the long-lived context saved in `init` (`this.acx`), never the render's `cx`, for notify and spawn

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
- Size, spacing and radius: `_lib/tokens.js`, whose comments say which brand
  value each token stands for.
- Shape of a control, card or label: `_ui/ui.js`.

Where `DESIGN.md` is silent, keep to those owners rather than inventing a value,
and record the gap in otto-nation/otto-nation.github.io's issue tracker.

## Checking and installing

- Check an assembled copy, not the source tree: `_herdr_app_install_mods "$tmp"` then `herdr-app check "$tmp/mods/team-prs"` (source `lib/ui.sh`, `lib/constants.sh` and `ai/herdr/steps.sh` first)
- Install live with `rm -rf ~/.config/herdr-app/mods/team-prs` then `_herdr_app_install_mods ~/.config/herdr-app`. Never patch the live files; edit the source and reinstall
- A reload briefly reports "no main.js"; `herdr-app status` settles to `"errors": []`

## Screenshots

- `herdr-app shot` races: select the tab (`herdr-app do tab.select --id <id>`), wait 8–10 s, shoot twice, then re-select the user's tab. Repeat if the shot shows another session
- Overlays do not appear in shots
