# PR board (web)

The web view of the `prs` board (`ai/herdr/app/mods/prs`). Spec and deploy contract: `docs/ai-automation.md` § PR board deploy contract.

- Logic lives in `ai/herdr/app/mods/_lib/` and is shared with the herdr-app mod: change it only in ways that keep the
  mod behaving the same and its `tests/herdr_app_mods_lib_*.bats` green.
- `lib/view.js` `boardModel().bar` comes from `_lib/filters.js` `barModel`; `public/filter_bar.js` draws it; filters persist in `localStorage` `pr-board.ui` via `prefs.js` `normalizeFilters`.
- Cards show state tags (Merged, In merge queue, Draft) top right and every `prIssues` problem except owed feedback
  as coloured status text in the badge row, independent of lane (`lib/view.js` `cardTags` / `cardBadges`); a draft keeps
  its real CI status. The web server does not read owed feedback.
- Import `_lib` as `../../app/mods/_lib/<file>.js`. Static URLs mirror `ai/herdr/`, so the same spelling works in the
  browser; the release tree (`bin/pr-board-release`) keeps the same paths.
- Only `server/main.js` touches `Bun`. Everything else is node-testable: `tests/herdr_web_*.bats` drive it with
  `node --input-type=module` through `tests/herdr_web_helper.bash`.
- The client never sends argv or paths: ids and `{repo, number}` only; the server finds the PR on its own board.
- Page DOM goes through `public/dom.js h()`; only `trustedMarkdown()` assigns markup, fed by `lib/markdown.js`.
- Look: root `AGENTS.md` § UI and styling (read the brand's `DESIGN.md`) and `ai/herdr/app/mods/AGENTS.md` § Design (which file
  owns each value). Here colours are `/gen/theme.css` variables (from `ai/herdr/app/themes/otto-*.generated.json`) and sizes
  and type roles are `/gen/tokens.css` (from `_lib/tokens.js`), both computed by `lib/css.js`; no literal colours or sizes in
  `public/` or `lib/`. `tests/herdr_web_style.bats` checks what a grep can.
- Loading: `public/load_status.js` reads `_lib/load_state.js`. After a second the header shows the load line over a native
  `<progress>`, a strip after the filters marks each repo, and lanes are static dashed placeholders until the first repo
  lands. No CSS animation on those indicators.
