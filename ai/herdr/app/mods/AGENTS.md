# herdr-app mods

Conventions for the mods under this directory (`team-prs`, `_lib`, …).

## Where things live

- Shared UI components: `_ui/ui.js` (installed into every mod as `./_ui/ui.js`, like `_lib/`). Reuse `button`, `chip`, `pill`, `inset`, `segment`/`segmented`, `filterGroup`, `eyebrow`, `card`, `hoverRow`, `pane`, `paneBar`, `emptyPane`, `scrollBody`, `placeholder`, `caption`, `ciMark`, `addStat`/`delStat`, `mono` before hand-building a chain; add a component there when a pattern appears a second time
- Every top-level `_*/` directory under `mods/` is a shared tree installed into every mod, so only put real libraries there (no scratch dirs like `_tmp/`)
- Sizes, spacing, fills and breakpoints: `_lib/tokens.js`. Colours come only from `theme()`. No literal pixel or colour values in views
- Pure logic: `_lib/*.js`, covered by `tests/herdr_app_mods_lib_*.bats`
- Handlers use the long-lived context saved in `init` (`this.acx`), never the render's `cx`, for notify and spawn

## Design

Read the brand's `DESIGN.md` before changing a view (root `AGENTS.md` § UI and
styling says where). The mods draw with herdr's `theme()`, which
`ai/herdr/app/palettes/otto.toml` fills from the brand tokens, so use the field
that carries the token's role. The table is a guide; `otto.toml` owns the
mapping, so if they disagree the palette wins and this table needs fixing:

| Brand token | `theme()` field | Use in a view |
| --- | --- | --- |
| canvas | `background` | Pane background |
| surface | `surface`, `raised` | A panel set apart from the canvas |
| ink | `foreground` | Body copy, titles |
| ink-muted | `muted` | Secondary copy, eyebrows, meta lines |
| hairline | `border` | Card edges, dividers |
| outline | `dim`, `input` | Control boundary only. Non-text (3:1): never body copy |
| done | `done` | Success, as text |
| warn | `working` | Warning or in progress, as text |
| rosa (light) / amarillo (dark) | `accent` | UI accents and large text only |
| anil | `ansi4` | Links |

Prefer the named field over an `ansiN` slot: the name says the role, the slot
only says where a terminal paints it.

The brand's rules, as they apply here:

- Flat. Hierarchy comes from hairline edges, never fills or shadows.
- A card has no fill and a hairline border; title `TEXT.title` bold, body
  `TEXT.small` in `muted`. `RADIUS.control` for controls, `RADIUS.card` for cards.
- A section label is an eyebrow: mono, caption size, `muted`.
- State is coloured text (`done`, `working`, `accent`). A bordered tag is for at
  most one label per row; a row of tags reads as noise.
- Amarillo is never text on the light canvas; rosa is never inline prose.

Where `DESIGN.md` is silent (type sizes, interaction states, a danger colour, a
third text tier, status glyphs), keep to `_lib/tokens.js` and the existing
`_ui/ui.js` components rather than inventing a new value, and add the gap to
otto-nation/otto-nation.github.io#23, which collects what the file lacks for app UI.

## Checking and installing

- Check an assembled copy, not the source tree: `_herdr_app_install_mods "$tmp"` then `herdr-app check "$tmp/mods/team-prs"` (source `lib/ui.sh`, `lib/constants.sh` and `ai/herdr/steps.sh` first)
- Install live with `rm -rf ~/.config/herdr-app/mods/team-prs` then `_herdr_app_install_mods ~/.config/herdr-app`. Never patch the live files; edit the source and reinstall
- A reload briefly reports "no main.js"; `herdr-app status` settles to `"errors": []`

## Screenshots

- `herdr-app shot` races: select the tab (`herdr-app do tab.select --id <id>`), wait 8–10 s, shoot twice, then re-select the user's tab. Repeat if the shot shows another session
- Overlays do not appear in shots
