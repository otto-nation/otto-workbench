# PR board (web)

The web view of the `prs` board (`ai/herdr/app/mods/prs`). Spec and deploy contract: `docs/ai-automation.md` § PR board deploy contract.

- Board pulls and the board view-model are shared with the mod and owned by `ai/herdr/app/mods/AGENTS.md` § Board code lives in `_lib`: `server/board.js` only discovers targets (`hc ls --json`) and schedules; `lib/view.js` adapts `_lib/board_model.js` `boardModel` to the page's UI state; `public/filter_bar.js` draws `boardModel().bar`; filters persist in `localStorage` `pr-board.ui` via `prefs.js` `normalizeFilters`. The page subscribes with the tab it shows (`public/api.js events(…, {view})`, reopened on a tab switch), which is how the server knows to read owed (the owner rule's Has Comments bullet).
- Auth: `PR_BOARD_TOKEN` on every request but `GET /healthz`, `GET /login`, `GET /login.css` and `POST /login`; sign in once at `/login` and the browser keeps a host-only session cookie.
- Transport: `GET /api/events?view=team|mine` (the page's tab; anything else is 400). A probe without `view` is Team. Lanes draw `boardModel()` lanes' `status`/`message` (loading, error) as the mod does.
- Snapshot privacy: `lib/snapshot.js` strips checkout paths (`hasCheckout` only), whitelist-copies review runs (`PUBLIC_RUN_KEYS`), and puts errors in words (`friendlyError`).
- Cards and detail draw `_lib/pr_status.js` `presentationOf` slots (`public/board_view.js card`, `public/detail_view.js header`); the compact list draws `public/board_view.js compactRow` from `row_badges` (prs/compact.js's row), and the header's load-error status is `_lib/load_state.js errorsStatus`.
  A draft keeps its real CI status.
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
