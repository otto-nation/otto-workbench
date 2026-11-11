---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---

<!-- doc-budget: 847 -->

# AI Automation

Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.

This page is the operator's view: what to install, what to run, and what the commands do.
Behaviour that belongs to one module — how findings are numbered, what the publishing gate covers, how a run's bookkeeping is keyed, what a supersession preflight reads — is documented on that module in [AI Libraries](ai-libraries.md), so there is one copy of it and it sits next to the code it describes.

## Setup

First-time setup:

```bash
ai/setup.sh
```

Prompts for confirmation at each step. Safe to re-run. This installs Claude Code configuration, rules, and agents, plus skills — shared between Claude Code (`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from the one `ai/skills/` tree.

rtk and worktrunk install through Homebrew where it is available. On a machine
without it, such as an unprivileged Linux user with no writable brew prefix,
they install through mise (`mise use -g rtk`, `mise use -g worktrunk`). When
neither is present, `install_package` first installs mise itself with its
own installer (`https://mise.run`), so a fresh Linux account needs nothing
beforehand. worktrunk is installed by the git component as well, since that
component configures it and `wt-init` runs it.

`ai/setup.sh` also scaffolds `~/.config/task/taskfile.env`, the GitHub PATs `pr create` and `pr describe --post` publish with (`GH_TOKEN`, optional `GH_TOKEN__<ORG>`). See `security-secrets.md` for the two-file secret model.

## What Gets Installed

<!-- include: bin/local/generate-tool-context --emit ai-installs -->

<!-- include: bin/local/generate-tool-context --emit skill-reference -->

<!-- include: bin/local/generate-tool-context --emit lifecycle -->

## Herdr

Selecting `herdr` in `ai/setup.sh` installs [herdr](https://herdr.dev/) with its
own installer (`~/.local/bin/herdr`) and, on every sync, runs `herdr update` and
reinstalls its Pi and Claude Code integrations for whichever harness is
configured. The integrations let herdr resume a Pi or Claude session after its
server restarts; Pi also reports working/blocked/idle directly.

Herdr is an operator tool. Agents get no permission to run it, so it never
competes with `job_start`/`run_in_background` as a way to spawn work.

- Create worktrees with `wt`, not `herdr worktree` — herdr's default
  `~/.herdr/worktrees` sits outside the workbench layout.
- After setup, register a remote machine with
  `herdr machine add <ssh-host> --label <name>`; the reminder prints during
  setup and `otto-workbench ai sync` while none is registered, not during a
  regular `otto-workbench sync`.
- Sync sets the herdr TUI's theme to Gruvbox, following the terminal's light/dark
  appearance: in `~/.config/herdr/config.toml` it owns only `[theme]`'s `name`,
  `auto_switch`, `light_name` and `dark_name` (`gruvbox` / `gruvbox-light`,
  `auto_switch = true`). Every other line — keybindings, `onboarding`,
  `[theme.custom]` — is left as it was. Picking a theme in herdr's Settings turns
  `auto_switch` off; the next `otto-workbench sync` turns it back on. A symlinked or
  unparseable file is left alone with a warning, and a change herdr's
  `herdr config check` rejects is rolled back. The rest of the file is not managed
  yet (#1660).
- The herdr desktop app (`herdr-app`) gets the workbench's Gruvbox themes wherever it is
  installed, herdr selected or not: sync links `ai/herdr/app/themes/<name>.generated.json`
  into `~/.config/herdr-app/themes/` as `<name>.json` and sets only `theme` in
  `~/.config/herdr-app/settings.json` to `{"light": "otto-light", "dark": "otto-dark"}`,
  following the system appearance; its colors come from the pinned `@otto-nation/brand` release (`bin/local/dev-deps` fetches it). Every other setting is left alone. A theme file you
  edited in place is kept with a warning; delete it to get the link back. If the app
  rejects a change it says why in `~/.config/herdr-app/settings.error`.
- Sync also installs the PR board mod (`ai/herdr/app/mods/prs`) into `~/.config/herdr-app/mods/` (and `~/.config/herdr-app-untrusted/mods/` once that exists, so both windows carry the same mods and layouts)
  as a real copy with `_lib/` and `_ui/` inside, keeps edits, and a `workbench` layout (main + a PRs toggle beside Files/Changes that opens the board as a right panel, with a Full screen button that gives the panel the whole work area; `herdr-app do layout.switch --name workbench`).
  The board refreshes every 3 minutes (remote review listings every 15 s), and the refresh icon at the right of its header pulls everything now.
  The board has This/All spaces; Team, Mine and the Batch page share one filter bar: Show (Drafts per tab, hidden by default; Landed; Empty lanes; Hide bots on Team), Repo when there is a choice, Label, and Author on Team; each value is included or hidden; active filters are removable tags with Clear filters; lanes with no PRs are hidden unless Empty lanes is on; in All spaces a Repo group filters by repository; a click opens the PR in the same pane with its review history and actions:
  review, re-run, recover, post, dismiss, cancel, and watch. Each but cancel and watch runs
  `pr review --pr N --repo-dir DIR` as a labelled `hc` terminal; watch shows the run output in the pane and can open it as a tab.
  A Files tab lists the PR's changed files (from `gh api …/pulls/N/files`, page 1 first then the rest in parallel) and shows a file's diff inline (first 1,500 lines); files marked `linguist-generated` in `.gitattributes` sort last and are left out of the header's file and line counts; Open for Review checks the PR out with `wt switch pr:N` and selects, or opens, a space on that worktree (this machine only).
  Mine's Batch button (with "running" beside it while runs are active) opens a Batch page: running runs with Log/Cancel/Kill, a Start form (your shown PRs by repo, under the shared filters, plus steps and AI and push options; it starts one `pr batch run` per repo), and recent runs to look back; cards show their batch state and a PR's detail has a Batch tab. Cards show state tags (Merged, In merge queue, Draft) top right and every problem as status text in any lane (× Conflicts, × CI failing, … CI running, · CI unknown, ! owed feedback, ! Changes requested); a draft keeps its real CI. Status is fetched in concurrent batches. The web publishes each repo as it loads and shows every `prIssues` problem except owed feedback. The lanes and each lane's Batch shortcut are described in `ai/herdr/app/mods/AGENTS.md`. While repos load, after a second the header reads "… Loading N of M repos" over a thin progress line, a strip beside the filters marks each repo queued (·), loading (…), stale (!) or failed (×), and lanes show static dashed placeholders until the first repo lands.
- **PR board on the web** (`ai/herdr/web/`): the same board in a browser or on a phone, for the repos the
  agent's herdr-core terminals sit in. A Bun server (`bun ai/herdr/web/server/main.js`) reuses
  `ai/herdr/app/mods/_lib/` for every fetch and decision, binds `127.0.0.1`, and requires its access
  token (`PR_BOARD_TOKEN`) on every request but `GET /healthz`, `GET /login`, `GET /login.css` and
  `POST /login`; sign in once at `/login` and the browser keeps a host-only session cookie. `ai/herdr/web/bin/pr-board-release --out DIR --ref REF` assembles a
  self-contained, offline-startable release tree (no dependencies). Environment, routes, health, auth
  behaviour, tools and memory: [PR board deploy contract](#pr-board-deploy-contract).
- `herdr-app` with no arguments opens the main window through `open -n` and returns, so the
  window is not tied to the terminal and its log stays out of it; a window that already
  answers `herdr-app status` is left alone. With arguments it runs Herdr's own CLI
  unchanged. Sync replaces the `~/.local/bin/herdr-app` link Herdr's installer makes
  (straight to the app binary) with this launcher; `HERDR_APP_BUNDLE` overrides
  `~/Applications/Herdr.app`.
- An isolated untrusted window holds agent machines: launch with `herdr-app-untrusted`
  (`HC_SESSION=untrusted`, `~/.config/herdr-app-untrusted`). It refuses a local
  terminal or `afar` grant. Sync copies appearance keys and `clipboard_write` from the main window, links keys/themes, seeds `shared.json` once. Edit
  appearance in the main window; machines are owned by homelab (`docs/herdr-agents.md`).

### PR board deploy contract

**8.1 Build**

From a checkout (or `git archive` export) of the pinned workbench ref, no network:

```bash
ai/herdr/web/bin/pr-board-release --out DIR --ref REF
```

- `DIR` must not exist or be an empty directory (else exit 2). `REF` must match `^[A-Za-z0-9._/-]+$`
  (exit 2). Copy failure exit 1. Needs bash ≥ 4.3, coreutils `cp`/`find`/`sort`, and `sha256sum` or
  `shasum`. Does not need Bun, node, git, or network. Idempotent in the sense that it refuses rather than
  merges.
- Output (exact):

```
DIR/
  VERSION                                   one line: REF
  MANIFEST.sha256                           "<sha256>  <path>" for every other file, LC_ALL=C sorted
  ai/herdr/app/mods/_lib/*.js               verbatim
  ai/herdr/app/themes/otto-light.generated.json
  ai/herdr/app/themes/otto-dark.generated.json
  ai/herdr/web/package.json
  ai/herdr/web/.bun-version
  ai/herdr/web/server/*.js
  ai/herdr/web/lib/*.js
  ai/herdr/web/public/**                    html, css, js
```

- No `node_modules`, no `.git`, nothing generated at build: CSS is computed from the theme JSON at start.
  The tree can be root-owned and read-only; the server never writes under it.
- Verify: `cd DIR && sha256sum -c MANIFEST.sha256` (or `shasum -a 256 -c`).

**8.2 Entrypoint**

```bash
bun DIR/ai/herdr/web/server/main.js
```

Bun ≥ 1.2; tested with the version in `DIR/ai/herdr/web/.bun-version` (`1.4.2`, the homelab's
`ovis_bun_version`). Working directory irrelevant. Logs to stdout/stderr (journal): one start line
`pr-board <version> listening on http://<host>:<port>`, tool-check warnings, refresh failures. Exits 2 on a
bad configuration (message on stderr), 0 on SIGTERM/SIGINT. No network at start: the first `gh` call
happens on the first pull, which runs only once a page connects.

**8.3 Environment**

| Var | Required | Default | Meaning |
|---|---|---|---|
| `PR_BOARD_TOKEN` | yes | — | ≥ 32 chars (use `openssl rand -hex 32`); start refused otherwise |
| `PR_BOARD_PORT` | yes | — | integer 0–65535 (0 = any free port, for tests) |
| `PR_BOARD_HOST` | no | `127.0.0.1` | must be `127.0.0.1`, `::1` or `localhost`; anything else refused |
| `PR_BOARD_HC` | no | `hc` (PATH) | path of the herdr-core CLI; on the homelab `/home/<agent>/.local/bin/herdr-core` |
| `PR_BOARD_STATE_DIR` | no | `$XDG_STATE_HOME/pr-board`, else `$HOME/.local/state/pr-board` | created 0700; will hold `actions.jsonl` (later phase; nothing writes it yet) |
| `PR_BOARD_NAME` | no | `this server` | header label and "where" in errors, e.g. `homelab · maximum` |
| `PR_BOARD_ORIGIN` | no (recommended) | — | exact external origin, e.g. `https://prs-maximum.lab.home`; else Origin host must equal `Host` |
| `PR_BOARD_OVIS_URL` | no | — | the agent's Ovis base URL, e.g. `https://ovis-maximum.lab.home/`; will enable "Open in Ovis" (later phase; not read yet) |
| `PR_BOARD_FULL_REFRESH_S` | no | `180` | full pull interval while a page is connected; integer 30–86400, else refused at start |
| `PR_BOARD_LISTING_REFRESH_S` | no | `15` | listing-only pull interval while a page is connected; integer 5–3600, else refused at start |
| `PR_BOARD_MAX_PROCS` | no | `4` | concurrent child processes; integer 1–32, else refused at start |
| `HOME` | yes (systemd sets it) | — | the agent's home: `gh` auth, `pr`'s state root, the herdr-core socket. Start needs it only when neither `PR_BOARD_STATE_DIR` nor `XDG_STATE_HOME` is set |
| `PATH` | yes | — | must resolve `pr` (workbench, not coreutils), `gh`, `git`, `wt`, and `hc` unless `PR_BOARD_HC` is set; put `$HOME/.local/bin` before `/usr/bin`. Not checked at start: a missing tool shows as a board error on the first pull |
| `HC_SESSION`, `XDG_*` | no | inherited | passed to `hc` unchanged if the agent's core uses them |

Empty string is treated as unset for every optional variable.

**8.4 Tools it shells out to**

All resolved through `PATH` (except `hc` → `PR_BOARD_HC`), run as the agent:

| Tool | Calls (phase) |
|---|---|
| `hc` / `herdr-core` | `ls --json` (repo discovery, 1), `ls --json --label otto.app=pr` + `get <t> --json` (busy, 1), `run --bg --json …` (2, 3, 4), `get <t> --json` (run follow-up, 2), `read <t> --lines 80` (2, 4), `kill <t>` (2), `exec --json --on …` (remote targets only) |
| `gh` | `api /user`, `api /repos/<o>/<n>/pulls`, `api graphql`, `api …/files`, patch pages (1); via `wt` (3) |
| `git` | `remote get-url origin`, `check-attr -z linguist-generated` (1) |
| `pr` | `pr review --list --schema-version 1`, `pr review --list --schema-version 1 --if-changed STAMP`, `pr batch status --full --offline` (1); `pr review` inside launched terminals (2); `pr batch status --list --schema-version 2`, `pr batch status RUN --full --offline --schema-version 2`, `pr batch cancel --kill RUN --schema-version 2` (4); `pr batch run --checkout DIR --prs KEYS --steps STEPS --with-review --with-ai STEPS --auto-publish ff --select SEL` inside launched terminals (4) |
| `tail` | `tail -n 200 <step log path>` (4; path from `pr batch status`) |
| `wt` | `switch pr:<n> --no-cd --format json` (3) |
| `sh` | `sh -c 'command -v pr; command -v gh; command -v wt; true'` (1) |
| `pi` | started by herdr-core in the Open-for-Review terminal (3) — resolved by the **core's** PATH |

**Requirement on herdr-core's environment (homelab side).** Terminals started by `hc run` get the core's
environment, not the board's, and local runs are not PATH-wrapped by `hcRunArgv`/`hcBatchArgv` (only remote
ones are). So the agent's `herdr-core.service` environment must resolve `pr`, `gh`, `git`, `wt`, `pi` to the
workbench ones, and carry whatever the AI agents `pr review` and `pr batch run --with-ai`/`--with-review`
spawn need (the same credentials reviews already use). Check as the agent:
`herdr-core exec -- sh -c 'command -v pr gh git wt pi'` must print `~/.local/bin/pr` (not `/usr/bin/pr`).

**8.5 Health and auth behaviour to probe**

- `curl -fsS http://127.0.0.1:$PR_BOARD_PORT/healthz` → `200 {"ok":true,"version":"<ref>"}` without a token;
  touches no tool, so it proves the server is up, not that herdr-core or GitHub are.
- `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PR_BOARD_PORT/` → `401`, and the same for
  `/api/board`. A deploy must fail if either is not `401`. (`/login.css` is public by design; do not probe
  it for `401`.)
- `curl -s -H "Authorization: Bearer $PR_BOARD_TOKEN" http://127.0.0.1:$PR_BOARD_PORT/api/board` → `200`.
- SSE: `/api/events` sends a `: ping` comment every 15 s; Bun's idle timeout is set to 60 s. Caddy flushes
  `text/event-stream` immediately; no `flush_interval` change needed.

**8.6 Memory**

Expected: server RSS 50–100 MB steady with a loaded board (Bun runtime + snapshot of a few hundred PRs ≈ 1–2 MB + files cache
for ≤ 10 PRs). Measured empty, Bun 1.4.4: ≈ 39 MB idle, ≈ 41 MB with a page and SSE client. Children run inside the unit's cgroup: `pr review --list` (Python, ≈ 60–100 MB while it
runs), `pr batch status` (Python, phase 4, every 15 s per machine while a page is connected), `gh`
(≈ 20–30 MB each), `git`, bounded by `PR_BOARD_MAX_PROCS=4`. Peak cgroup ≈ 250–300 MB during a full pull.
Recommendation: `MemoryMax=384M`, `OOMPolicy=stop`. Terminals launched with `hc run` (reviews, batch runs,
pi) live in herdr-core's cgroup, not the board's. Phase 1 measures and records actual figures in the PR.

**8.7 Behaviour the unit should know**

- Start order: `Wants=herdr-core.service` and `After=herdr-core.service` (§ 7.7).
- Restart: stateless (`actions.jsonl` arrives in a later phase); `Restart=on-failure` is safe. Sessions survive restarts
  (cookie derived from the token). A restart forgets which batch terminal belongs to which repo (the Log
  button), not the runs themselves: those are `pr batch`'s state.
- Polling (board and batch) stops when no page is connected; an idle instance makes no `gh` or `pr` calls.
- The session cookie gets `Secure` only when the request carries `X-Forwarded-Proto: https`, which Caddy's `reverse_proxy` sends by default.

## Configuration

### GitHub owners

Every GitHub write is checked against `github.owners` and refused elsewhere; unset refuses all. Record yours with `otto-workbench config set github.owners.work '[your-org]'`. Published text may not name an owner in a different group — see `general.md` § Cross-Group References. A login listed in a group is an owner like any organisation, so `@login` is a reference — including the `@reviewer` attributions `pr comments` writes. The policy and where it is enforced: [`gh/owners.py`](ai-libraries.md#ghownerspy).

The user's overrides for publishing anyway are `WORKBENCH_ALLOW_UPSTREAM=1` (target) and `WORKBENCH_ALLOW_CROSS_REF=1` (text). Both are honoured only on user-run Python paths (`gh.client`, Linear, the git hooks); the agent guards never read them, and an agent command that names either is refused.

The global `commit-msg` hook is advisory (`git commit --no-verify` / `-n` skips it). The global `pre-push` hook is the gate: it scans commits new to the destination remote against that destination's owner (`git push --no-verify` skips it). Non-GitHub destinations and unconfigured owners allow. A repo-local `core.hooksPath` (husky and similar) bypasses both hooks, but every push the workbench makes (`pr create`, `pr push`, `pr rebase`, fix passes) runs the pre-push check itself, `--no-verify` or not. Both fail open when the check cannot run. See [`git/hooks/commit-msg`](../git/hooks/commit-msg) and [`git/hooks/pre-push`](../git/hooks/pre-push). Annotated tag messages are not scanned.

### Which model each phase runs on

`otto-workbench config phases [--json] [DIR]` reports each phase's model and thinking level, and which setting answered. `DIR` names another repo. Example: `otto-workbench config phases --json`. Exit 0 when the config loads (unset tiers are data), 1 when a file cannot be read, 2 on usage. JSON schema and source layers: [`agent/phase_report.py`](ai-libraries.md#agentphase_reportpy). Precedence: [`agent/phases.py`](ai-libraries.md#agentphasespy).

### Usage ledger

Every AI call made through the workbench records what it cost. Query the ledger
with `otto-log stats`:

```bash
otto-log stats                      # last 7 days, grouped by script
otto-log stats --since 24h          # any h/d/m window
otto-log stats --by task            # or: script, model, day, phase
otto-log stats --by day --json      # one JSON object per row
```

What is recorded, and what each column means, is documented on
[`agent/usage.py`](ai-libraries.md#agentusagepy).

### Evaluating AI quality

The ledger says what a call cost; the eval harness says what it bought. `eval-models`
runs each case in [`eval/corpus/`](../eval/corpus/) through a throwaway git repo and
scores the result against that case's `manifest.json`.

```bash
eval-models --dry-run                          # validate the corpus, run nothing
eval-models --models claude-sonnet-5,grok-4.7 --runs 3   # compare models
eval-models --compare                          # diff against eval/results/ baselines
eval-models --save-baselines                   # record new baselines
```

A manifest's `task` field picks how its case is run and scored, and each task
pairs a runner with a scorer in `ai/lib/eval/scoring_<task>.py`. What a case of
each task holds is documented on [`eval/task.py`](ai-libraries.md#evaltaskpy);
how each is scored, on the task's own module —
[`review`](ai-libraries.md#evalscoring_reviewpy),
[`ci-fix`](ai-libraries.md#evalscoring_cifixpy),
[`skill`](ai-libraries.md#evalscoring_skillpy),
[`disprove`](ai-libraries.md#evalscoring_disprovepy), [`verify`](ai-libraries.md#evalscoring_verifypy), [`synthesis`](ai-libraries.md#evalscoring_synthesispy). The runner, the fixture repo, and the
statistics over repeated runs are shared and know nothing about any one task.

Landing a case costs a full eval run. `bin/local/validate-eval-baselines` fails
any corpus entry that no baseline in `eval/results/` covers, and
`bin/local/validate-all` discovers it by glob — so a new case is red on pre-push
and in CI from the moment it lands until a baseline records it. That baseline has
to come from a run over the whole corpus: `--save-baselines` rebuilds each
model's file wholesale from the entries of the run it is handed, so
`--entry <new-case> --save-baselines` writes a file holding that one entry and
drops every other. There is no filtered top-up. A name the on-disk baseline
does not yet carry, or a first run under a new backend, is a first floor —
`--save-baselines` records it; `validate-eval-floors` still fails an unfloored
entry on disk. Retire a case by deleting it from `eval/corpus/`, every
`{backend}-{model}.json`, and every stem in `floors.json` together: a floor
with no baseline is dropped, a baseline with no floor is unfloored, a corpus
case with no baseline fails `validate-eval-baselines`. Partial deletion fails
by design.

`--compare` diffs a run against same-backend `{backend}-{model}.json` files
and exits `2` on a regression; `--save-baselines` records the served model and
exits `3` when unresolved or a run never executed; `--seed-floors` rebuilds a
missing `floors.json` from this run's scores, not git history (`best` is the
current values — a decayed run collapses the high-water mark) and warns when
it reseeds. Which metrics gate, and why a dead run is not a score, are on
[`eval/scoring.py`](ai-libraries.md#evalscoringpy). The
[`Eval` workflow](../.github/workflows/eval.yml) runs this weekly and on demand
— not a pull-request check: each run spends real money on real model calls, and
without `ANTHROPIC_API_KEY` it validates the corpus and stops.

### Checking the rules still reach an agent

Every agent the pipeline runs gets its coding rules through an undocumented
`--bare`/`--add-dir` coupling, which can break with no error and no missing file.

```bash
rules-canary            # exit 0 rules arrive, 1 they do not, 2 could not measure
rules-canary --json     # both readings, the delta, and the floor
```

How it measures that, and why it is a difference of two runs, are on
[`eval/rules_canary.py`](ai-libraries.md#evalrules_canarypy). The
[`Eval` workflow](../.github/workflows/eval.yml) runs it ahead of the corpus and
reports it separately — a canary that cannot measure should not cancel the
week's eval.

### Where a review lives: review.json and the rendered review.md

A review is a typed record, `review.json`, written atomically beside
`meta.json` in the review directory. `review.md` is rendered from the two on
every change and carries a "rendered from review.json" banner — edit it and the
next write overwrites the edit; nothing reads it back.

- **What the review says** — summary (an assessment, not a recap), findings
  and their dispositions, questions for the author, what the reviewer
  verified sound, file triage, agent failures, static analysis — is the
  record's ([`review/record.py`](ai-libraries.md#reviewrecordpy)).
- **What the run was** — head SHA, mode, status, dates, generator, and the models its agents ran on (read once from the usage ledger) — is `meta.json`'s; the header at the top of `review.md` is rendered from it, and the posted comment ends with the same `Models:` line when the comment has anything else to say.
- **A finding's disposition** — open, fixed, declined, skipped, unreached,
  falsified — replaces the checkbox and annotation ledgers.
  `review-orchestrate --track` files the findings a fix pass left skipped or
  unreached.
- **Agent output** is JSON. Each phase writes one artifact ([`review/artifacts.py`](ai-libraries.md#reviewartifactspy)) — `scout.json`, `holistic.json`, `group-N.json`, `single.json`/`synthesis.json`, `disprove.json` — shaped like the prompt example generated from the dataclass the reader checks. Ingest ([`review/ingest.py`](ai-libraries.md#reviewingestpy)) reads it once: invalid JSON (line and column), a schema violation or a malformed entry (JSON path) is rejected with one repair turn; a second rejection is an agent failure.
- **A review directory with no `review.json`** is stale. The next `pr review` regenerates it from scratch; a re-review of it runs full rather than incremental.
- **A schema-1 `review.json`/`prior.json`** is upgraded on load, keeping posted stable IDs, so re-review stays incremental with prior findings and declines. For that round a finding whose location the old parser never placed is a whole-PR finding. Any other unknown schema is stale. A run interrupted before the upgrade restarts from scratch on resume.
- `pr review --repair` re-syncs when `review.json` already loads. It rebuilds from `group-N.json` only when there is no readable `review.json`, skipping a group file that no longer loads; a directory holding only pre-upgrade `group-N.md` files cannot be rebuilt — re-run the review.
- `pr review --post --event approve` / `request_changes` submits the review as
  an approval or a change request (implies `--submit`). Refused when the PR
  head moved since the review was written; post as a comment, or re-run.
- `pr review --dismiss` records the latest run as dismissed, with no GitHub
  call. Refused for a run already posted.
- `pr review --list --schema-version 1` rows carry `post` and `history`, and
  the document carries `reviews_root` and `stamp`. `--if-changed STAMP` answers
  `{"unchanged": true}` with no `reviews` when nothing under the reviews root
  changed since the document that carried that stamp. `review.archive_keep`
  (default 3) is how many earlier runs `history` keeps.

Questions post under the Summary and verified notes in a collapsed block after
the findings; neither is a finding, so neither counts toward the verdict or
reaches a fix pass. A malformed question or verified note is dropped with a
warning.

### How self-review rounds converge

A `pr review --self --fix` round ends with a fix commit that moves HEAD. What
keeps the next round from starting over:

- **A qualifying fix commit counts as reviewed.** The header carries
  `covered_sha` when that commit landed on the reviewed commit, checks were
  green, every fix was verified, it touches only the fixed findings' files
  and their tests, and no file of a finding the pass left unfixed. That SHA
  counts as reviewed; a later review still measures its delta from `head_sha`.
- **Re-reviews do only new work.** Same-HEAD dirty files (including ones the
  last round read that have since been reverted) are incremental; `--force`
  keeps the run full. New nits outside the delta are dropped; new nits inside
  it stay as `held_nits` until a full review. A prior decline on an unchanged
  file is restated without an agent. A sticky skip keeps a finding away from
  the fix agent while its anchor is unchanged — it does not settle it.
- **Verify names inherited failures.** A red check re-run at the pass's base
  that was already red becomes `Fix-Checks: preexisting` only when every
  failure is inherited; one new failure keeps it red. The fixes stay and
  publishing is still held.
- **`pr fix` skips the review** when only declined, skipped or held findings
  remain.
- **An interrupted pass resumes.** The next run on the same HEAD stashes the
  leftovers and, with `--fix`, resumes the fix step; `--force` or a failed
  set-aside does not resume.
- **A raced commit is recorded** as `head_moved_to`; the review covers the
  pinned head only.

Module detail: [`review/convergence.py`](ai-libraries.md#reviewconvergencepy),
[`fix/baseline.py`](ai-libraries.md#fixbaselinepy),
[`review/fix_logs.py`](ai-libraries.md#reviewfix_logspy).

### Steering a review: focus and the related issue

`pr review --focus TEXT` / `pr review --focus-file PATH` name areas the review
must cover (repeatable; each text is 4 KB, refused when larger, at most 5). Phase 1
(scout/holistic) derives 1–3 more; when neither runs, the writing agent does.
Single, group and synthesis see them — groups only the areas whose paths fall
in their files, plus pathless ones. Scout and holistic see the requested areas
so they complement them. Disprove does not. The writer records coverage per
area. An unassessed area renders *Not assessed this round.* A missing coverage
line gets one repair turn; a second miss is published that way with a warning.
A re-review without `--focus` keeps the prior requested areas and stays
incremental. A different `--focus` replaces them and runs full — that round
has no change set. `--issue` puts the issue's link and body in the prompt,
capped at 8 KB, and synthesis sees it.

Module detail: [`review/focus.py`](ai-libraries.md#reviewfocuspy).

### How a review's verdict is decided

`ReviewVerdict` owns the three verdicts in both spellings: the word the line
states (`Request changes`) and the value `pr status` shows (`changes_requested`).
One member owns both, so the markdown cannot say one thing while the dashboard
reports another.

The verdict is derived, never written. One function reads the findings, how the
run ended and its mode:

- A self-review states none — it is advisory and has no PR to approve or block.
- An error run states none, and the line says why (`No part of this review ran`).
- Any open must-fix → Request changes; any open should-fix → Needs discussion;
  otherwise Approve.
- A partial run keeps Request changes / Needs discussion and adds "Review
  incomplete"; where it would approve it states no verdict, because an
  incomplete run cannot vouch that nothing blocks.

Open means not fixed: declined, skipped and unreached findings still count, and
the line counts declined ones apart. Nits and idioms never count.

Both `review.md` and the posted comment state it as one generated line closing
the Summary (`**Verdict: Needs discussion.** 1 should-fix open.`). Agents write
none: prompts never see the line, and a writer's `verdict` key is a schema
violation. `pr status`, the listing and herdr record the same value, so they
cannot disagree.

An objection to the approach is a must-fix finding about the PR as a whole;
there is no Disapprove.

Posting still uses the event the operator chose (`--event`); nothing maps the
verdict to an approval. Readiness blocks on open must-fix counts, not on the
verdict.

Review, comments and CI fixes, and `already_addressed` verdicts, are checked by
one verify gate before they land or post: [`fix/gate.py`](ai-libraries.md#fixgatepy).

### What pr rebase replays

`pr rebase` skips a prefix already in the base (`git rebase --onto <base>
<fork-point>`) and may fold the rest into one commit first. `rebase.squash`
is `auto` (the default), `always`, or `never`; opt out with
`otto-workbench config set rebase.squash never --project`.

Where the replay starts: [`rebase/fork_point.py`](ai-libraries.md#rebasefork_pointpy).
When a squash happens: [`rebase/squash.py`](ai-libraries.md#rebasesquashpy).

### Which files the rebase fix pass is allowed to touch

When a rebase's force-push is rejected by a pre-push check, `pr rebase` runs a
fix pass over the conflicted files, using the project's own check output as the
oracle. The candidate list is every file the rebase resolved a conflict in —
which on a long branch is dozens of files, listed once per conflict rather than
once per file, while the check that failed names two of them.

The pass runs only on the candidates the check output names, matching either the
path or the bare filename so a check that reports a basename still scopes. Each
fix is a whole-file prompt, so the unscoped version spent a call per resolved
file; worse, the backend runs with `acceptEdits` and `Bash(*)`, so handing it a
file the check had no complaint about is an invitation to rewrite a file the
branch never touched. When the output names no candidate at all — a check can
fail without printing a path — every resolved file is still a suspect and the
pass falls back to all of them, recording that it did so, because that is the
expensive path and it should not be invisible in the trail.

A fix that fails records its exit code as a trail error, not only as a console
line. A run where every fix fails is otherwise indistinguishable in the trail
from a run where the fix pass had nothing to do.

### Already addressed, or addressed in response

The `already_addressed` verdict means the code does what the reviewer asked. It
does not mean their comment was moot, and the two are not the same claim.
Triage reads code context from current HEAD, which already holds whatever the
pass fixed earlier in the same cycle, so a re-run re-triages a thread it fixed
on round one and gets `already_addressed` for it on round two — correctly. What
was wrong was the reply: a flat `Already addressed` told a reviewer their point
needed no action while the paragraph below it cited a commit made after they
made it ([#815](https://github.com/otto-nation/otto-workbench/issues/815)).

So the reply and the summary row ask when the code became true, relative to
when the thread was opened:

| The branch shows | Reply | Summary cell |
|---|---|---|
| a commit on the thread's line, dated after the review comment | `Applied:` … `Fixed in <sha>` | `Fixed in <sha>`, counted with the fixes |
| a commit on that line, dated before the comment | `Already addressed:` … `Addressed in <sha>` | `Already addressed` |
| no commit on that line — the code predates the branch | `Already addressed:` | `Already addressed` |
| the thread's line no longer means what it did when it was read | `Already addressed:` | `Already addressed` |

The commit is the one `git log -L` names for the thread's line, so two threads
on one file get two answers, and both the line and the date it is judged by are
checked so a rebase cannot invent one — `pr/attribution.py` carries the rules.
Either timestamp missing reads as pre-existing: claiming credit for a fix is the
assertion that needs evidence, and there is none when one side cannot be dated.

`pr comments --finish` reaches this reply from a second direction. A fixed
thread whose commit the resolver cannot cite — a hook rejected the pass's
commit, so nothing was recorded — is routed here for the linkless body
([#827](https://github.com/otto-nation/otto-workbench/issues/827)). The pass
demonstrably acted on that thread, which is what put it in `fixed`, so it keeps
the in-response reading and names no commit: the one the branch offers for that
line predates the comment and cannot be what carried a fix made after it.

### A rebase renames the held commit, it does not unpublish it

A fix pass that holds its push records the SHA it committed, and the run that
clears the hold often comes *after* a rebase — `pr rebase --fix` is what a
supersession warning tells the operator to run. The rewrite leaves that SHA
resolvable as an object and contained by no branch, the one shape "has this been
pushed?" answers wrong
([#952](https://github.com/otto-nation/otto-workbench/issues/952)). So `--finish`
follows the rename first, matching an orphan to its replay on patch id — every
pass commits under one static subject, so content is the only field that tells
two of them apart — and cites the branch's own history from there. Nothing
clears on a guess: a commit still on the branch is unpushed for the ordinary
reason, and an orphan with no replay, or with two, holds too and says how to
recover. Re-running `--fix` is not the way; it discards reviewed replies.

### The summary comments are the record, not the state file

The `Review Comments Addressed` comments are what a reviewer reads to confirm
their feedback was accounted for, and they are the one place a cycle is tallied.
A round that still has the last word edits its own summary in place; a round a
reviewer has spoken over posts a new one and links back to the earlier ones. The
record is therefore the *set* of summary comments on the PR, and each rule below
is read against that set rather than against one body — a row is safe when some
comment on the PR still carries it, not only when the newest one does.

Three things follow, and the first two were bugs —
[#714](https://github.com/otto-nation/otto-workbench/issues/714) and
[#712](https://github.com/otto-nation/otto-workbench/issues/712):

**Every outcome is reported, including the ones nobody resolved.** A
`needs_human` thread is the case that took the most operator judgment, so
omitting it is the worst row to lose. It renders as open, with its reason, in
every summary a round posts — an open question a reader has to walk the comment
chain to find is one they will not find. If the operator settled it outside the
tool, `--finish` reconciles the snapshot against GitHub first and grades what
it finds: a reply of ours naming the verdict credits a fix, a bare resolution
records `settled_elsewhere` — no fixed tally, no commit, nothing owed.

**A decomposed comment item reconciles through the comment it came from.** An
item split out of a top-level comment has a synthetic id and no review thread,
so there is no thread state to read it from
([#776](https://github.com/otto-nation/otto-workbench/issues/776)). `--finish`
asks the same question of its source instead: a reply of ours further down the
PR that opens `Applied:` / `Already addressed` /
`Suggestion reviewed and determined to be inapplicable` and cites
the source comment's permalink settles the item, exactly as the equivalent reply
on a thread settles a thread. An item that restates an inline thread settles
with that thread, and renders as one row rather than the same point twice under
two outcomes — the thread is the copy that stays, since that is where the reply
lands and where resolution is recorded. An item nothing on GitHub answers still
renders as open and still holds the summary back.

**Rows the local state file cannot account for are carried forward.** State is
per-target and per-worktree, and a round routinely runs without the state that
covered an earlier one: `pr gc`, a pruned state directory, a recreated worktree,
another machine. Overwriting a comment with a body built purely from state then
deletes rounds nobody can recover. So an edit reads its target's body first,
matches it row by row on the thread permalink, and re-emits anything
unaccounted for verbatim, counted as `N carried over` and warned about on the
run. Carrying forward is scoped to the comment being replaced, because that is
the only comment an edit can destroy: a round that posts a new comment takes
nothing away, so it has nothing to carry.

A comment therefore only grows for as long as rounds keep editing it. A row no
later round reproduces keeps its last rendered state rather than vanishing from
the comment holding it — a stale row a reviewer can still read beats a round
nobody can recover.

**An Action cell written by hand is never re-rendered.** Carrying rows forward
protects a row state cannot account for; it cannot protect the Action cell,
because the row key deliberately excludes it — a round changing that cell
(deferred to fixed) has to count as the same row. The Action cell is also the
only cell a person edits, so the two rules once composed into the inverse of the
intent: a hand edit survived exactly as long as local state had lost the thread,
and was overwritten the round state regained it. So each published row is asked
a second question. If its Action cell opens with none of the wordings this
renderer writes, a person wrote it: the published row is re-emitted in the
position its entry would have taken, counted as `N hand-written`, and the run
warns with the cell it kept and the cell it would have written instead. Every
summary comment is searched, not just the newest — a cell edited on round one's
comment is the usual case once round two has posted its own, and the newest
occurrence of a row wins, so restoring a generated cell by hand hands the row
back to the renderer.

The header counts follow the cells. An entry whose row is held drops out of its
own bucket, so a row reading `Superseded — …` no longer sits under a header
reading `1 need discussion` — the contradiction that reopened a question the PR
had closed. Nothing tries to read a classification back out of the prose a human
wrote; the row simply stops being counted as anything but hand-written. Hand a
row back to the renderer by restoring a generated cell on the published comment
with `gh api -X PATCH`.

**A summary that has been answered is reposted, not edited.** GitHub leaves an
edited comment where it was and notifies nobody, so once a reviewer has
commented, submitted a review, or replied on a thread below the summary, editing
it writes the round's outcome somewhere the reader has already scrolled past.
Each round compares the summary's `created_at` against the newest activity that
is not ours — our own thread replies do not count, or the fix pass would trip
the check on itself every round — and posts a fresh comment when it lost the
last word. The fresh body carries the marker, so the next round finds it; the
superseded comment is left untouched as that round's record.

**A fresh summary describes the round that produced it**
([#924](https://github.com/otto-nation/otto-workbench/issues/924)). Restating
every thread the PR ever had made each new comment a complete record and an
unreadable one — the reader could not tell this round's work from what was
settled three rounds ago, and every reviewer was re-notified with mostly stale
content. A row may be left where it was published only when all three hold: some
summary comment on the PR already carries it, the comment this round is writing
is not one of them, and no one but us has spoken on the thread since the newest
summary went up. A thread nobody can date is written rather than skipped, since
"cannot tell" must not read as "settled". The skipped rows are counted in a note
under the table, and the round's own footer links every earlier summary comment
in order, so a reader landing on the newest one can walk the chain back through
the rounds it does not restate.

**A review body is answered in the summary.** GitHub has no reply API for one,
so `pr comments --reply <reviewId | rb-<reviewId> | rb-<reviewId>-<n>>
--body-file F --post` publishes one marked block per review under
`### Replies to review comments`. Without `--post` nothing is stored and the
draft is printed (no body if a push is still pending). A block is written when
no summary holds the review's block, re-rendered on an in-place edit, and
restated on a fresh comment only when the block changed; unlike a row, there
is no activity test. A hand-edited block is kept with a warning unless `--reply`
for that review replaces it. A dismissal reason (operator, fix pass or triage)
adds `Dismissed: <finding> — <reason>`, making a block even with no reply.

### Running from a different directory

When your CWD is not the target repo (e.g., running from a Claude Code session rooted in a different project), pass `--repo-dir` (or `--branch`) to `pr`:

```bash
pr create --draft --closes 941 --repo-dir /path/to/worktree
```

`--branch <b>` from a checkout that is not on `<b>` does not switch the current
tree. What happens instead depends on the command:

| Outcome | Commands |
|---|---|
| Create `<b>`'s worktree | `rebase`, `ci --fix`, `comments --fix`, `fix` |
| Refuse with the `require_worktree` message | `push`, `create`, `describe`, `comments` (dashboard), `comments --triage/--settle/--finish`, `comments --fix --finish`, `comments --reply` to a review body, `rebase --abort/--push-only` |
| Degrade to cached state with a dim note | `status` |
| Work, create nothing | `ci` (dashboard), `review` (the context resolver creates nothing; `review --self` and `review N` still make their own checkouts through review/worktree.py), `gc`, `comments --reply` to a thread |

A related hazard exists one level down, for the AI subprocess: a backend CLI
inherits the launching process's working directory unless it is told otherwise.
Every `ai_backend` entry point therefore takes a required `cwd` — see
[`agent/backend.py`](ai-libraries.md#agentbackendpy).

### Running a branch's own libraries

The Python entry points take their own pin, since `~/.local/bin/pr` is a symlink into
`main/` and `Path(__file__).resolve()` follows it — see `ai/bin/_libdir.py`:

```bash
WORKBENCH_AI_LIB_DIR=/path/to/worktree pr review --self --fix
```

### How `pr` decides what a bare token is

`pr` reads the delegate's own parser — `value_taking_options` in
[`core/tool_parser.py`](ai-libraries.md#coretool_parserpy) — before deciding whether a
bare token is the command's target or some other flag's argument. Without it,
`pr comments --reply 3777767789` reads the reply ID as a PR number and swallows
it. `PARSER_FACTORIES` in [`ai/lib/cli/dispatch.py`](../ai/lib/cli/dispatch.py)
names each delegate's `build_parser`.

One delegate needs no scan at all. `pr create` declares no positional: its
target comes from the global `--branch`/`--repo-dir`, already resolved into the
context before `cli.pr_create` sees its argv. So `create` declares
`takes_target=False` and the positional scan is skipped for it entirely — every
bare token in `pr create --title "…" --body-file …` belongs to the flag before it,
including a title that happens to be a number.

That leaves `takes_target` a hand-maintained declaration, so the commands it does
*not* excuse are guarded: `status`, `fix` and `gc` have no delegate either, and their
scan is arity-blind for the same reason. A test reads `value_taking_options` off each
of their subparsers — the same function `pr` classifies with — and fails the build the
day one declares an option that consumes a value, naming the two ways out: declare the
command as taking no target, or give it a delegate to read.

### pr batch

`pr batch` rebases, checks CI and notes comments on your open PRs. AI and
review are opt-in. Every step is a child `pr` (CI: `ci-check`) in a new
session with stdin closed, and runs drafted. Nothing is pushed unless `--auto-publish ff`.

```bash
pr batch plan --checkout DIR …           # which PRs need which steps (JSON)
pr batch run --checkout DIR … [--with-ai=rebase,ci,comments] [--with-review] [--auto-publish ff] [--rebase behind]
pr batch run --plan FILE …               # start from a saved plan
pr batch resume [RUN_ID] [--steps S]     # continue; --steps may only narrow the run's steps
pr batch report [RUN_ID] [--offline] [--format json|md]  # per-PR session commands; files under the run dir
pr batch status [RUN_ID]                 # the run report (JSON); --offline skips GitHub
pr batch status [RUN_ID] --full          # the raw run state plus `run_dir`
pr batch status --list                   # this machine's runs, newest first (JSON)
pr batch cancel [RUN_ID] [--kill]        # final; JSON `queued` when a holder applies it; `release RUN PR [--kill]` frees one PR
```

`--focus` is forwarded to every review step and replayed by `resume` (`--steps` only narrows).

Default steps are `rebase`, `ci`, `comments` (`review` needs `--with-review`),
each only where needed (`run` plans for itself; `run --help` has recipes). `run --prs` reads the named PRs by
number, ten to a request; without it the plan searches every open PR of yours,
ten to a page, since GitHub fails a request that runs past about ten seconds. A PR that needs no
step is still planned into the run when its saved `pr` state owes a closeout,
but only when `comments` is one of the run's steps and there is no `--select`. It is queued and gets a worktree like any other item. `rebase` is needed
only when the branch conflicts with its PR's base: `git merge-tree` over fetched
objects (a run keeps them in `refs/pr-batch/`), falling back to GitHub's merge
state, where only `DIRTY` counts. A branch that is behind but merges cleanly is
left alone. `--rebase behind` takes every branch behind its base instead
(fallback: `BEHIND`, `DIRTY` and `UNKNOWN` count); the run records the choice,
so `resume` keeps it. `comments` is needed when `pr comments` would find something to answer (`pr.owed_feedback`): threads, review bodies or PR comments.
`ci` runs `ci-check --no-rebase --head-sha <planned remote head>` (not `pr ci`)
when the rollup failed, with `--wait` if still running; `--fix` and the rate-limit
rerun only under `--with-ai=ci`. Fork heads skip both. A stacked PR records
`stack_wait_base` and is not rebased in the run. One fetching rebase or CI step per repo at a time per run;
several runs may share a repo (see troubleshooting). No start in a dirty worktree except restoring generated files.

A default run never opens a decision; items end `done` or `needs_session` with
`findings` (`batch/findings.py`), shown by `pr batch report`. A stored waiting
v1 run converts on `resume` (one-way). `--auto-publish ff` pushes a clean
fast-forward (`pr push --expect`) when an item closes with no findings; never a
force push, closeout post or issue filing. `--no-verify` skips the pre-push hook
on those publishes.

`pr batch status` prints one compact report. `--full` is the raw run state plus `run_dir`.
Keys: `run` (`id`, `status`, `active`, `exit_hint`, `hint`, `pending_requests`,
`failed_requests`), `counts` (items by status), `items` (non-terminal only),
`github`, `sessions` (`pr batch report` only). `exit_hint` is 0 once finished,
1 when it stopped without settling, null while a driver holds the run.
`run_summary` is `run`, `counts`, `report_path`. Events include `finding_created`
(`run`, `item`, `reason`, `step`). Exit 0 or 1; widening `resume --steps` is 2.

`pr batch status` and `pr batch report` carry `github` unless `--offline`. Keys and checkout rules are in
[`batch/live.py`](ai-libraries.md#batchlivepy).

Step stderr stays in the run's `logs/`; `run --verbose` and `resume --verbose`
also stream it as `step_log` events. `resume` continues an interrupted run; a
recorded cancel is final and resume refuses it.

Admission counts every batch driver on the machine: a step starts when free memory, less other
drivers' pending steps and `batch.mem_reserve`, covers its observed peak and pressure stays under
`batch.cpu_pressure_max` / `batch.mem_pressure_max`. Drivers that queued first start first, and with
nothing running anywhere the queue's head starts one step regardless ([`batch/machine.py`](ai-libraries.md#batchmachinepy)). `--pool` and `batch.pool_max` (default 2)
cap a run; with no pressure metrics (macOS) `batch.pool_default` (1) caps steps machine-wide. State lives under
`~/.local/state/workbench/batch/<run-id>/` (`state.json` is authoritative; `requests/` is the inbox); `pr gc` removes finished runs and orphaned `refs/pr-batch/` namespaces that have no live claim and no run ([`batch/gc.py`](ai-libraries.md#batchgcpy)).

### pr status

Without `--schema-version`, stdout is the state dump (or empty when there is none) and the dashboard is on stderr. `pr --schema-version 1 status` wraps it in `{schema_version, state_file, state}` with `state` null when the branch has no state yet.

### pr stack

`pr stack` prints the repo's open PRs as a stack graph (`{schema_version, trunk, nodes, cycles}`). Parent authority, in order: an open native GitHub stack (closed stacks do not count), the PR's base branch when another open PR owns it, `git.topology.stack_parent`, else the trunk. Health is `up-to-date`, `needs-rebase`, `has-conflicts`, `base-closed`, or `unknown`. `--pr N` prints that PR's ladder, bottom first.

### pr board

`pr board --schema-version 1 --checkout DIR [--checkout DIR ...] --phase listing|status|owed [--prs N,N] [--if-changed STAMP]` prints the board document (facts, lanes, badges, columns as JSON). Missing `--schema-version` exits 2. `--prs` only with `--phase status` (else exit 2). Exit 0 whenever a document prints; per-repo failures live in `repos[].error`. `--if-changed` matching the stamp returns `{schema_version, phase, stamp, unchanged: true}`. Python owns every fact, lane, badge and column; no draft CI exemption; clients hide a row whose effective team lane is `landed`. `complete` is false for `status --prs` and `owed` (clients merge by `(repo, number)`).

### The Pi package the sync declares, and who gets it

Pi reads *and writes* `~/.pi/agent/settings.json` — `pi install`, `pi config` and Ctrl+S in `/model` all land there — so `step_pi_settings` ([`ai/pi/steps.sh`](../ai/pi/steps.sh)) merges the workbench's template into that file rather than copying over it. Scalar keys are seeds: one the live file already carries stays as whatever set it first, so a changed template default never reaches a machine that has the key; delete it there to be re-seeded. Model keys come from `models.*` instead (`otto-workbench config models`), and per-model compaction reserves from `context.compact_at` (`otto-workbench config context`, written by `step_pi_compaction` after the package refresh). `models.session: auto` starts Pi on `workbench/auto`, the virtual model the `model-router` extension registers to route each request to a tier, and Claude Code on `opusplan`. A
`defaultModel` or `enabledModels` entry `pi --list-models` does not list is a
sync warning, not a failure, whoever wrote it. That check (`step_pi_models`)
runs after the package refresh and asks the user's own pi from `$HOME`, so neither
a stale extension clone nor a repo's pinned pi reads as a wrong id. AI runs pick
their pi per repo: the managed install, or the repo's mise pin when it is newer
([`agent/pi_binary.py`](ai-libraries.md#agentpi_binarypy)), started as `<node> <pi script>` with a node that is not a mise shim, which would apply the repo's trusted mise `[env]` (its `GOOGLE_CLOUD_PROJECT`, say) to pi itself. A provider with no rows
warns that it did not load: Vertex extensions skip registration without
`GOOGLE_CLOUD_PROJECT` or an ADC file, usually a shell older than `~/.env.local`.

`packages` is reconciled instead, because a list gains an entry without
displacing one. Before declaring one, the sync asks GitHub whether this machine
can fetch it — `gh api repos/<owner>/<repo>`. Three answers, two of which act:

| Verdict | What the sync does |
|---|---|
| the repo answers | declares the package |
| a definitive 404 | withdraws it, so Pi stops retrying a clone it cannot complete |
| no `gh`, no auth, no network | leaves the entry however the live file has it |

The third row is why the check is not a boolean: a sync run offline must neither
install a package it could not verify nor strip one that already works. It asks
about the repo rather than the owning org because a user account's public repo
has no org, and a restricted repo in one this machine belongs to still cannot be
cloned. What extensions expose to a run is decided in
[`agent/backend_pi.py`](ai-libraries.md#agentbackend_pipy); how an existing entry
is recognised is one [`sync-settings.jq`](../ai/pi/sync-settings.jq) documents.

### Five shared foundations under `ai/`

Five modules exist because the same decision was being re-made at every call
site, and the spread was the bug. Each owns its own reference page:

| Module | Takes over | Reference |
|---|---|---|
| `proc` | Running a subprocess, and what a failure is allowed to say — stderr included. | [`core/proc.py`](ai-libraries.md#coreprocpy) |
| `timeouts` | How long a subprocess may run, chosen as a tier rather than a number. | [`core/timeouts.py`](ai-libraries.md#coretimeoutspy) |
| `git_client` | Invoking `git` — `cwd`, capture, non-zero handling, and per-subcommand config. | [`git/client.py`](ai-libraries.md#gitclientpy) |
| `push` | Pushing, and asking the remote whether it actually took it. | [`git/push.py`](ai-libraries.md#gitpushpy) |
| `land` | Committing a pass's work and pushing it, as one act with one result. | [`git/land.py`](ai-libraries.md#gitlandpy) |

They stack: `land` sits on `push` and `git_client`, which sit on `proc`, which
requires a `timeouts` tier on every call — `bin/local/validate-timeouts` enforces
that one across `ai/`, so a new subprocess call cannot skip the question. `pr create`
pushes through `push` by way of `pr.branch_sync`, and a push typed by hand is recorded by the global `pre-push` hook for `pr` to reconcile.

## Guidelines & Rules

The workbench resolves a layered rule system, which every harness it installs
reads from. A later layer wins a name, and the operator's is last:

- **Repo defaults** ([`ai/guidelines/rules/`](../ai/guidelines/rules/)) — path-scoped rules that auto-load based on file type, plus always-on, harness-neutral rules. [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) and [`git.generated.md`](../ai/guidelines/rules/git.generated.md) are derived from registries and conventions
- **Generated** (`~/.local/state/workbench/rules/`) — `workbench.md`, rewritten on every sync with this machine's paths baked in
- **Operator overrides** (`~/.config/workbench/overrides/ai/guidelines/rules/`) — a same-named `<domain>.md` replaces the layer below, a `<domain>.local.md` is carried alongside it, and a `<domain>.disabled` sentinel suppresses it entirely

`resolve_rules` ([`lib/files.sh`](../lib/files.sh)) merges the three, and each
harness installs that one set the way it wants it — which is what lets a machine
with only one of them installed still get its rules. Add machine-specific rules
with `workbench-rules add <domain> "rule text"`; `list` and `status` show what
this machine has added.

`step_claude_rules` ([`ai/claude/steps.sh`](../ai/claude/steps.sh)) symlinks the
merged set into `~/.claude/rules/`. Pi reads one context file per directory
rather than a rules directory, so `step_pi_guidelines`
([`ai/pi/steps.sh`](../ai/pi/steps.sh)) concatenates the same set — minus
anything `paths:`-scoped or `harness:`-excluded from `pi` — behind the
`ai/pi/AGENTS.head.md` preamble, into `~/.pi/agent/AGENTS.md`.
Write `~/.pi/agent/AGENTS.override.md` to replace it; the workbench never
touches that file. See `rules-authoring.md` § Which harnesses a rule reaches
for the full frontmatter table.

## Scaffolding a New Project

After cloning a repo, scaffold Claude Code configuration for it:

```bash
otto-workbench ai init          # scaffold .claude/ in the current repo
otto-workbench ai init --force  # re-scaffold an existing project
```

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates), plus a root `AGENTS.md` — the file every harness reads (Claude Code natively from 2.1.277, which otto-workbench's AGENTS.md support assumes; `ai sync` warns on an older one). A repo still on `CLAUDE.md` keeps working: `ai init` leaves it alone and suggests `git mv CLAUDE.md AGENTS.md`, and the SessionStart hook and `ai sync` repeat the suggestion. Nothing renames it for you.
