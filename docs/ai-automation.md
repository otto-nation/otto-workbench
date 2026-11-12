---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---
<!-- Generated from docs/ai-automation.src.md by bin/local/compose-docs — do not edit. -->

<!-- doc-budget: 895 -->

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

**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)

**Skills:** analyze-project, anatomy, architecture, ceiling-debt, ci-failures, diverge, dream, finishing-a-development-branch, machine, pr-batch, pr-comments, pr-rebase, promote, reference, retro, self-review-fix, using-git-worktrees, wiki, wiki-capture, writing-skills — see [Skill Reference](#skill-reference) for invocation, output, and lifecycle details.

**Agents:**

| Agent | Description |
|-------|-------------|
| changelog | Generate categorized release notes and changelogs from git history. Used by task automation. |
| debugger | Systematic code-level bug diagnosis. Read-only — traces through source code to find root causes. Never modifies anything. Dispatch when a bug needs a diagnosis before anyone changes code. Not for a session that will implement the fix: that is superpowers:systematic-debugging, whose last phase implements, and loading both is duplicated process rather than escalation. |
| explain | Fast text-in/text-out explainer. Answers questions from provided input without exploring files or suggesting edits. |
| incident | Structured production incident investigation. Read-only triage — gathers symptoms, checks recent changes, forms ranked hypotheses. Never modifies anything. |
| migrate | Analyze codebases for migration tasks and produce phased upgrade plans. Read-only — plans changes but does not apply them. |
| reviewer | Structured code review for PRs and diffs. Read-only — produces categorized findings (must-fix, should-fix, nit). Never modifies anything. |
| reviewer-lite | Lightweight code reviewer for group and angles phases. Receives pre-collected data — no context gathering needed. Produces categorized findings (must-fix, should-fix, nit). Never modifies anything. |

**MCP Servers:** otto-workbench

## Skill Reference

### `/analyze-project`

Analyze a project's codebase and populate scaffolded AGENTS.md and .claude/rules/ files with project-specific conventions. TRIGGER when: user runs otto-workbench ai init, re-scaffolds with --force, or has empty AGENTS.md or .claude/rules/ sections.

```
/analyze-project
```

**Output:** `AGENTS.md, .claude/rules/`
**Trigger:** Run after otto-workbench ai init scaffolds a project, after --force re-scaffolds, or when AGENTS.md or .claude/rules/ files have empty sections.

### `/anatomy`

Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. TRIGGER when: user wants an overview of codebase structure, before exploring an unfamiliar project, or after significant file changes. SKIP: user asks about a specific known file — read it directly.

```
/anatomy
```

**Output:** `.claude/anatomy.md`
**Auto-trigger:** on HEAD change (via Stop hook)
**Trigger:** Run to refresh the project file index before exploring an unfamiliar codebase, or after significant file changes.
**Skip:** Do not use when the user asks about a specific file they already know — just read it directly.

### `/architecture`

On-demand architecture.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .claude/architecture.md. TRIGGER when: user discovers wrong-software assumptions, adds a new service or role, or architecture.md is stale (last-reviewed >14 days). SKIP: memory consolidation (use dream); machine-level facts (use machine).

```
/architecture
```

**Output:** `.claude/architecture.md`
**Trigger:** Run after discovering wrong-software assumptions, adding a new service or role to a project, when architecture.md last-reviewed date is more than 14 days old, or after discovering container tool constraints.
**Skip:** Do not use for memory consolidation (use dream instead) or machine-level facts (use machine instead).

### `/ceiling-debt`

Scan for ceiling: markers and present the debt ledger. TRIGGER when: user asks about ceilings, deferred simplifications, or technical debt markers. SKIP: general tech debt discussion without ceiling markers.

```
/ceiling-debt
```

**Output:** `ceiling debt ledger to stdout (manual); .claude/ceiling-debt.md (auto)`
**Auto-trigger:** on-stop (via Stop hook)
**Trigger:** ceiling debt, show ceilings, what did we defer, list simplifications, ceiling markers
**Skip:** General tech debt discussion, architecture review, non-code requests

### `/ci-failures [<pr_number_or_run_id_or_branch>]`

Diagnose and fix GitHub Actions CI failures with run-aware progression tracking: fetch, classify, diagnose, fix, push, and monitor across workflow runs. TRIGGER when: user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red. SKIP: reviewing code (use code-review or pr review instead); addressing PR review comments (use pr-comments instead).

```
/ci-failures [<pr_number_or_run_id_or_branch>]
```
**Output schema:** `pr ci --tool-schema`
**Trigger:** Use when user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red.
**Skip:** Do not use for code review (use code-review or pr review instead); do not use for addressing PR review comments (use pr-comments instead).

### `/diverge`

Generate options from isolated parallel branches, then critique them in a separate pass — for a decision where the first answer is likely an anchor. Runs only when named. TRIGGER when: user types /diverge or asks to run diverge on a problem. SKIP: any request that does not name it; one-canonical-answer questions; bugs with a known root cause; choosing between options the user already listed.

```
/diverge
```
**Trigger:** Use when the user explicitly invokes /diverge or asks to run diverge on an open-ended design, naming or strategy question.
**Skip:** Do not use unless the user names it; do not use for questions with one canonical answer, bugs with a known root cause, or a choice between options already on the table.

### `/dream`

Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. TRIGGER when: user asks to consolidate memory, clean up notes, or after sessions with corrections and decisions. SKIP: project architecture facts (use architecture); machine profile updates (use machine).

```
/dream
```

**Output:** `memory/ topic files`
**Auto-trigger:** 24h (via Stop hook, or the maintenance timer)
**Trigger:** Run to consolidate scattered memory notes, after multiple sessions with corrections or decisions, or when MEMORY.md is cluttered. Auto-triggers once 24h and 5 sessions have both passed since the last run.
**Skip:** Do not use for project architecture facts (use architecture instead) or machine profile updates (use machine instead).

### `/finishing-a-development-branch`

Complete a development branch — verify tests, self-review, then open a draft PR. TRIGGER when: implementation is complete and the work needs to reach main. SKIP: work still in progress; a branch whose PR is already open.

```
/finishing-a-development-branch
```
**Trigger:** Use when implementation is complete and the work needs to reach main, or when the user asks to finish, wrap up, or ship a branch.
**Skip:** Do not use while work is still in progress, or on a branch whose PR is already open — prefer a follow-up PR.

### `/machine`

Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. TRIGGER when: user upgrades tools, installs new runtimes, or machine.md is stale (>7 days). SKIP: project-specific architecture (use architecture); memory consolidation (use dream).

```
/machine
```

**Output:** `~/.claude/machine/machine.md`
**Auto-trigger:** 24h (via Stop hook)
**Trigger:** Run after upgrading runtimes, installing new tools, or when machine.md last-updated is more than 7 days old. Auto-triggers every 24h.
**Skip:** Do not use for project-specific architecture (use architecture instead) or memory consolidation (use dream instead).

### `/pr-batch`

Sweep many open PRs with pr batch and turn the result into focused per-PR sessions. TRIGGER when: updating, rebasing, triaging or sweeping several open PRs, or running pr batch. SKIP: a single PR (use pr-rebase, pr-comments, ci-failures).

```
/pr-batch
```
**Trigger:** Use when updating, rebasing, triaging or sweeping several open PRs, or running pr batch.
**Skip:** Do not use for a single PR (use pr-rebase, pr-comments, or ci-failures instead).

### `/pr-comments [<pr_number_or_branch>]`

Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, then publish clean rounds directly and draft contested ones for approval. TRIGGER when: user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments. SKIP: initial code review requests (use code-review or pr review instead); self-review before PR creation (use self-review-fix instead).

```
/pr-comments [<pr_number_or_branch>]
```
**Trigger:** Use when user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments.
**Skip:** Do not use for initial code review requests (use code-review or pr review instead); do not use for self-review before PR creation (use self-review-fix instead).

### `/pr-rebase [branch] [--fix] [--no-push] [--stop-on-conflict] [--force-landed] [--onto|--base <ref>] [--fork-point <ref>] [--no-verify]`

Rebase onto the branch's base with generated-only conflict rebuild or abort; --fix for AI resolution. TRIGGER when: user asks to rebase a branch, update it onto its base, squash-and-rebase, resolve rebase conflicts, or says hand rebase / rebase by hand. SKIP: simple git pull --rebase with no conflicts; commit rewording.

```
/pr-rebase [branch] [--fix] [--no-push] [--stop-on-conflict] [--force-landed] [--onto|--base <ref>] [--fork-point <ref>] [--no-verify]
```
**Output schema:** `pr rebase --tool-schema`
**Trigger:** Use when the user asks to rebase a branch, update it onto its base, squash-and-rebase, resolve rebase conflicts, or says hand rebase / rebase by hand.
**Skip:** Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording.

### `/promote`

Reviews accumulated Claude Code memories for promotion into durable workbench artifacts — lint rules, scripts, coding rules, hooks. Prioritizes mechanical enforcement over prose. TRIGGER when: user wants to review memories for promotion, or after dream has consolidated corrections. SKIP: direct rule/script edits — just edit them; memory consolidation (use dream).

```
/promote
```

**Output:** `ai/memory/PROMOTE.md`
**Auto-trigger:** 7 days (via Stop hook, or the maintenance timer)
**Trigger:** Run to evaluate accumulated memories for promotion into workbench artifacts, or after dream has consolidated several sessions of corrections and decisions. Auto-triggers once 7 days and 10 sessions have both passed since the last run.
**Skip:** Do not use when the user wants to directly edit a rule or script — just edit it. Do not use for memory consolidation (use dream instead).

### `/reference`

Show a reference card of all workbench skills, agents, and reuse modes. TRIGGER when: user asks what skills/commands/agents are available, wants a quick reference, or asks how to use the workbench. SKIP: detailed help on a specific skill (invoke that skill directly).

```
/reference
```

**Output:** `formatted reference card to stdout`
**Trigger:** what skills are available, show commands, help, reference card, what can you do
**Skip:** Detailed help on a specific skill — invoke that skill directly

### `/retro`

Analyze PR review comments to identify gaps in coding rules. Fetches comments from all registered repos, classifies them against existing rules, and proposes specific rule additions or refinements. TRIGGER when: user wants to analyze review patterns for rule gaps, after a batch of PR reviews. SKIP: addressing comments on a specific PR (use pr-comments); memory consolidation (use dream).

```
/retro
```

**Output:** `ai/memory/RETRO.md`
**Auto-trigger:** 72h (via Stop hook, or the maintenance timer)
**Trigger:** Run to analyze recent PR review comments for coding rule gaps, after a round of PR reviews has been completed, or when rule coverage feels incomplete. Auto-triggers once 72h and 5 sessions have both passed since the last run.
**Skip:** Do not use when the user wants to address comments on a specific PR (use pr-comments instead). Do not use for memory consolidation (use dream instead).

### `/self-review-fix [branch_name]`

Run self-review and auto-fix findings. Wraps pr review --self --fix --push. Can also fix from an existing review without re-running. TRIGGER when: user asks to self-review a branch, run pre-merge review, or auto-fix findings before PR creation. SKIP: reviewing someone else's PR (use code-review or review); addressing existing PR review comments (use pr-comments).

```
/self-review-fix [branch_name]
```
**Trigger:** Use when the user asks to self-review a branch, run a pre-merge review, or auto-fix review findings before creating a PR.
**Skip:** Do not use for reviewing someone else's PR (use code-review or review instead). Do not use for addressing existing PR review comments (use pr-comments instead).

### `/using-git-worktrees`

Ensure work happens in an isolated worktree before implementation begins. TRIGGER when: starting feature work that needs isolation, or before executing an implementation plan. SKIP: read-only exploration, which needs no worktree.

```
/using-git-worktrees
```
**Trigger:** Use when starting feature work that needs isolation, before executing an implementation plan, or when the user asks to set up a worktree.
**Skip:** Do not use for read-only work — searching, reading, and exploring are exempt from this machine's worktree rules.

### `/wiki [init|ingest|compile|status|lint|signals|promote|archive] [args]`

Build and maintain a compiled knowledge base — ingest sources, compile them into interlinked articles, query them, and keep them healthy. TRIGGER when: user wants a knowledge base, asks to ingest or compile a source, queries compiled knowledge, or asks about wiki health. SKIP: one-off questions answerable from the codebase; project docs that belong in docs/.

```
/wiki [init|ingest|compile|status|lint|signals|promote|archive] [args]
```

**Output:** `articles in the knowledge base directory; reports to stdout`
**Trigger:** wiki, knowledge base, ingest a source, compile articles, query the wiki, wiki health
**Skip:** Questions answerable directly from the codebase; documentation that belongs in the project's own docs/

### `/wiki-capture`

Reviews the session that just ended for anything worth keeping and logs it to the knowledge base, without writing articles. TRIGGER when: a session ends in a repo with a knowledge base and a capture is due. SKIP: writing or editing articles (use wiki compile); one-off answers already in the codebase.

```
/wiki-capture
```

**Output:** `SESSION_OBSERVATION entries appended to the knowledge base's _log.md`
**Auto-trigger:** 24h (via Stop hook, or the maintenance timer)
**Trigger:** Auto-triggers at session end in repos that have a knowledge base, once 24h and 3 sessions have both passed since the last capture.
**Skip:** Never writes or edits an article — /wiki compile processes what this logs, deliberately.

### `/writing-skills`

Write or change a skill in this workbench — SKILL.md frontmatter, the agent: contract, and the generators to re-run. TRIGGER when: creating a skill, editing a SKILL.md, changing lifecycle hooks, or asked how to write a skill. SKIP: invoking an existing skill; authoring a rule (see rules-authoring).

```
/writing-skills
```
**Trigger:** Use when creating a new skill, editing an existing SKILL.md, changing auto-triggered lifecycle behavior, or when the user asks how to write or author a skill.
**Skip:** Do not use for invoking a skill that already exists, or for authoring a coding rule (see rules-authoring.md).

## Session Lifecycle

Skills with a cadence and a `should-<skill>.sh` gate (shown in the table above) auto-trigger from two places — Claude Code's Stop hooks in `settings.json`, and the maintenance timer:

1. **Session exit** — Stop hooks run `should-<skill>.sh` cooldown checks
2. **If due** — `run-auto-task <skill>` detaches a headless `claude -p` session, which inherits the exiting session's working directory and logs to `~/.claude/logs/`
3. **Skill completion** — completion script records a timestamp so the cooldown resets

The headless session runs the full hook set and reaches its own Stop hook, so `run-auto-task` exports `WORKBENCH_AUTO_TASK` and every gate exits 1 when it sees it. That is what stops the cascade. It replaced `--bare`, which skipped hooks but also broke slash-command resolution, so the spawned session answered `Unknown command: /<skill>` and did nothing.

The Stop hook only fires under Claude Code, so the same gates are asked again by `maintenance/bin/run-due-auto-tasks` on the 12h maintenance timer — otherwise a machine working in another harness trips nothing. The gates are cooldowns over file mtimes rather than events, so the timer is a true second caller and not an approximation of one. It spawns at most one task per cycle, longest cooldown first, and skips silently where Claude Code is not installed.

Additionally, `wt-cleanup --quiet` runs on every session exit to remove stale git worktrees.

### Manual triggers

Every lifecycle skill can be run on demand by its invocation in the Skill Reference above.

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
  Mine's Batch button (with "running" beside it while runs are active) opens a Batch page: running runs with Log/Cancel/Kill, a Start form (your shown PRs by repo, under the shared filters, plus steps and AI and push options; it starts one `pr batch run` per repo), and recent runs to look back; cards show their batch state and a PR's detail has a Batch tab; run, step and item states are `_lib/batch.js` `runStatus`/`stepStatus` and `pr_status.js` `itemStatus`. Cards show state tags (Merged, In merge queue, Draft) top right and every problem as status text in any lane (× Conflicts, × CI failing, … CI running, · CI unknown, · CI loading, ! owed feedback, ! Changes requested), then a running review and the batch state; positive state (CI passing, Approved) shows only in a PR's detail, and every view reads `_lib/pr_status.js` `presentationOf` on `pr board`'s per-row `presentation`; a draft keeps its real CI. Status is fetched in concurrent batches. The web reads the same `pr board` document through the same `_lib` store and model, so it shows owed feedback and lanes as the app does, and publishes each repo as it loads. Owed (Has Comments) is read only while a view shows Mine and is on screen; until it lands the lane reads "Loading comments…", and a failed read shows its reason. The lanes and each lane's Batch shortcut are described in `ai/herdr/app/mods/AGENTS.md`. While repos load, after a second the header reads "… Loading N of M repos" over a thin progress line, a strip beside the filters marks each repo queued (·), loading (…), stale (!) or failed (×); the header's stale · age, failed or N failed is `_lib/load_state.js` `errorsStatus`, shared with the web, and lanes show static dashed placeholders until the first repo lands.
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
bad configuration (message on stderr), 0 on SIGTERM/SIGINT. No network at start: the first tool call
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
| `gh` | `api …/files`, patch pages (1); `pr board` runs its own `gh` calls as its children; via `wt` (3) |
| `git` | `check-attr -z linguist-generated` (1) |
| `pr` | `pr board --schema-version 1 --checkout DIR --phase listing\|status\|owed [--offline] [--if-changed STAMP]` (app and web: one per target per pull, serialized per machine; `owed` only while a page shows Mine, at most once a minute per repo) (1); `pr review` inside launched terminals (2); `pr batch status --list --schema-version 2`, `pr batch status RUN --full --offline --schema-version 2`, `pr batch cancel --kill RUN --schema-version 2` (4); `pr batch run --checkout DIR --prs KEYS --steps STEPS --with-review --with-ai STEPS --auto-publish ff --select SEL` inside launched terminals (4) |
| `tail` | `tail -n 200 <step log path>` (4; path from `pr batch status`) |
| `wt` | `switch pr:<n> --no-cd --format json` (3) |
| `sh` | `sh -c 'command -v pr; command -v gh; command -v wt; true'` (1) |
| `pi` | started by herdr-core in the Open-for-Review terminal (3) — resolved by the **core's** PATH |

The agent's `pr` must be at or after `d42eb4152` (`pr board --offline`); an older `pr` shows as `outdated` on every repo, never a partial board. Each `pr board` can run up to 4 `gh` children that `PR_BOARD_MAX_PROCS` does not bound.

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
- SSE: `/api/events?view=team|mine` (the page's tab; anything else is 400) sends a `: ping` comment every 15 s; Bun's idle timeout is set to 60 s. Caddy flushes
  `text/event-stream` immediately; no `flush_interval` change needed.

**8.6 Memory**

Expected: server RSS 50–100 MB steady with a loaded board (Bun runtime + snapshot of a few hundred PRs ≈ 1–2 MB + files cache
for ≤ 10 PRs). Measured empty, Bun 1.4.4: ≈ 39 MB idle, ≈ 41 MB with a page and SSE client. Children run inside the unit's cgroup: `pr board` (Python, ≈ 60–100 MB per running phase, with up to 4 `gh` children of its own that `PR_BOARD_MAX_PROCS` does not bound; the `owed` phase runs only while a page shows Mine, so a Team-only or idle board never pays for it), `pr batch status` (Python, phase 4, every 15 s per machine while a page is connected), `gh`
(≈ 20–30 MB each), `git`, bounded by `PR_BOARD_MAX_PROCS=4`. Peak cgroup ≈ 250–300 MB during a full pull.
Recommendation: `MemoryMax=384M`, `OOMPolicy=stop`. Terminals launched with `hc run` (reviews, batch runs,
pi) live in herdr-core's cgroup, not the board's. Not re-measured after the shared store; Phase 1's figures stand until Task 11 measures.

**8.7 Behaviour the unit should know**

- Start order: `Wants=herdr-core.service` and `After=herdr-core.service` (§ 7.7).
- Restart: stateless (`actions.jsonl` arrives in a later phase); `Restart=on-failure` is safe. Sessions survive restarts
  (cookie derived from the token). A restart forgets which batch terminal belongs to which repo (the Log
  button), not the runs themselves: those are `pr batch`'s state.
- Polling (board and batch) stops when no page is connected; an idle instance makes no `gh` or `pr` calls; owed (`pr board --phase owed`) runs only while a connected page shows Mine.
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
  green, every fix was verified (incl. a test for behaviour changes), it touches only the fixed findings' files
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
- **A gate that could not record does not demote.** A failed or malformed
  checklist write leaves items as claimed, unverified (`could not record
  answers … not demoted`). Only a recorded broken verdict demotes.
- **Checks heartbeat.** `fix.verify_timeout` (default 900s) bounds the repo's
  checks; a timeout is "did not answer", not red. `fix.verify_progress_interval`
  (default 60s, `<= 0` disables) prints `checks still running — Ns of …s
  (fix.verify_timeout)`. The base-commit re-run gets its own timeout, so the
  worst case is twice the bound.
- **`pr fix` skips the review** when only declined, skipped or held findings
  remain.
- **An interrupted pass resumes.** The next run on the same HEAD stashes the
  leftovers and, with `--fix`, resumes the fix step; `--force` or a failed
  set-aside does not resume. A comments `--fix` records leftovers instead and
  resumes matching blobs; see [Closing out comments](#closing-out-comments).
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
A verdict the gate could not write down stays unverified and is never demoted.

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
Triage reads the working tree; a dirty or unpushed tree demotes
`already_addressed` and `invalid` until the tree is pushed. What was wrong was
the reply: a flat `Already addressed` told a reviewer their point needed no
action while the paragraph below it cited a commit made after they made it
([#815](https://github.com/otto-nation/otto-workbench/issues/815)).

So the reply and the summary row ask when the code became true, relative to
when the thread was opened. Already-addressed rows never cite that date's
commit:

| The branch shows | Reply | Summary cell |
|---|---|---|
| a commit on the thread's line, dated after the review comment | `Applied:` … `Current behaviour is at <permalink>` | `Fix applied (commit not recorded)`, counted with the fixes |
| a commit on that line, dated before the comment | `Already addressed:` … `Current behaviour is at <permalink>` | `Already addressed` |
| no commit on that line — the code predates the branch | `Already addressed:` | `Already addressed` |
| the thread's line no longer means what it did when it was read | `Already addressed:` | `Already addressed` |

The newest commit `git log -L` names for the thread's line only dates the
reading and is never cited. Both the line and the date are checked so a rebase
cannot invent a reading — `pr/attribution.py` carries the rules. A missing
timestamp reads as pre-existing: claiming credit needs evidence.

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

### Closing out comments

`--finish --post` refuses before resolving threads when the fix commit is still
unpublished. The last stderr line is `nothing was published: push <sha> first —
pr push --expect <origin tip> — then re-run pr comments --finish --post` (exit
1, no JSON). A successful bare `--finish` also prints no JSON; `--fix --finish`
still does.

`--settle <id>` records a thread that is on the PR even when it is missing from
the fix snapshot (`recorded as … (not in the fix snapshot — taken from the
PR)`). An id found neither place is still refused.

Closeout warns `<id>: left open — <reason>` for FIXED/ALREADY_ADDRESSED threads
it did not resolve. Every reason except `not on the PR` keeps reply debt, so
`pr status` still shows closeout owed. Open unanswered threads with no snapshot
row are listed with `pr comments --fix` or `pr comments --settle`.

A comments `--fix` stopped mid-agent records leftovers and, on the next comments
pass at the same HEAD, resumes matching blobs so they can be committed. An empty
round will not commit those leftovers.

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

### When two pr runs race

A second `pr` against the same target or checkout while one still holds the lock
exits 75 (`EX_TEMPFAIL`, the same value as `pr gc` when the GitHub hourly quota
is spent). The error names the owning command; retry once that run finishes.

### pr command contracts

What each `pr` command writes to origin or GitHub and how, what each exit means, and the
next step after one, rendered from the command's `CONTRACT` (`pr <command> --help` prints
the same text). Lease semantics for `pr rebase` and `pr push` live here and nowhere else.
To finish a rebase `pr rebase` completed but did not push, run `pr rebase --push-only`;
for one finished outside `pr`, add `--expect <full SHA>` as its `Next:` block says.

#### pr rebase contract

Rebase the branch onto its base, handle conflicts, and force-push the result under an explicit lease. Never edits the PR on GitHub.

**Writes remotely**

| Trigger | Writes | Where | How | If refused |
|---|---|---|---|---|
| `(bare invocation)` | refs/heads/<branch> := the rebased HEAD | origin | git push [--no-verify] --force-with-lease=refs/heads/<branch>:<expect>, where <expect> is origin/<branch> as this checkout saw it before the run's own fetch (empty when origin has no such branch, which creates it). The git pre-push hook runs unless --no-verify; the workbench owner check (gh.owner_hooks.refuse_outgoing) runs either way. | origin moved since the lease was taken: git reports stale info, nothing is written, exit 1. Resume line: git ls-remote origin refs/heads/<branch>. A refused pre-push hook leaves the rebase complete and unpushed (JSON force_pushed false). |
| `--fix` | the same push, after AI conflict resolution | origin | git push [--no-verify] --force-with-lease=refs/heads/<branch>:<expect> (same lease as the bare invocation) | origin moved since the lease was taken: git reports stale info, nothing is written, exit 1. Resume line: git ls-remote origin refs/heads/<branch>. |
| `--push-only` | refs/heads/<branch> := current HEAD, commits added since the rebase included; no rebase runs | origin | git push [--no-verify] --force-with-lease=refs/heads/<branch>:<expect>, where <expect> is RebaseSummary.lease_expect that the last completed rebase saved in the target's state.json | No recorded rebase: exit 1. A recorded empty lease while origin has the branch: exit 1 ('recorded lease is stale'). origin moved since the lease was taken: git reports stale info, nothing is written, exit 1. Resume line: git ls-remote origin refs/heads/<branch>. |
| `--push-only --expect` | refs/heads/<branch> := current HEAD; no rebase runs | origin | git push [--no-verify] --force-with-lease=refs/heads/<branch>:<expect>, where <expect> is exactly the SHA given (use the full SHA); no recorded rebase is needed | origin moved since the lease was taken: git reports stale info, nothing is written, exit 1. Resume line: git ls-remote origin refs/heads/<branch>. |
| `--no-verify` | nothing extra: changes how the push above runs | origin | adds --no-verify to git push, skipping the git pre-push hook; the owner check still runs. For a hook failure already understood — a flake, or a check the branch did not cause | as for the push it modifies |

**Exit codes**

| Code | Meaning | Next |
|---|---|---|
| `0` | rebased, and pushed unless --no-push (or a one-sided resolution held the push) |  |
| `1` | pre-flight failure, git error, refused or lost push, missing or stale recorded lease | `read the ✗ line and the Resume line above` → `if the JSON reads "status": "completed" with "force_pushed": false, commit the fix for what the hook named, then: pr rebase --push-only --branch {branch}` |
| `2` | flags that cannot combine (--push-only with --fix/--abort/--no-push, --expect without --push-only, --fix with --stop-on-conflict) |  |
| `3` | conflicts need a person; ConflictReport on stdout, its tree says paused or clean | *paused:* `resolve each file in `files` hunk by hunk (never take a side whole)` → `git -C {worktree} add <file> for each resolved file` → `git -C {worktree} rebase --continue (repeat until the rebase finishes)` → `git -C {worktree} ls-remote origin refs/heads/{branch} — it must equal git -C {worktree} rev-parse refs/remotes/origin/{branch}; if not, someone pushed since the rebase started: stop` → `pr rebase --push-only --expect <that full SHA> --branch {branch}` → `(or let pr finish instead: pr rebase --fix --branch {branch} resumes here)`<br>*clean:* `the rebase was aborted; HEAD is back at pre_rebase_head` → `pr rebase --fix --branch {branch} to resolve with AI, or pr rebase --stop-on-conflict --branch {branch} to pause and resolve by hand` |
| `4` | refused before changing anything (already landed, unrelated history, conflicts over budget); RefusalReport on stdout | `read `signal` in the JSON — already_landed usually means delete the branch` → `to rebase anyway: pr rebase --force-landed --branch {branch}` |
| `75` | another pr run holds this branch or checkout; retry once it finishes |  |

**Examples**

- `pr rebase --fix` — rebase onto the PR's base, AI-resolve conflicts, force-push under the lease
- `pr rebase --no-push` — rebase only; the push command is printed
- `pr rebase --push-only` — push what a --no-push run, or a run whose push failed, left — under its recorded lease
- `pr rebase --push-only --expect 0123456789abcdef0123456789abcdef01234567` — push a branch finished outside pr, leasing on the SHA origin must hold
- `pr rebase --stop-on-conflict` — pause on conflicts to resolve by hand
- `pr rebase --onto origin/release/1.2` — rebase onto a ref, used verbatim
- `pr rebase --force-landed --fix` — rebase even though the work landed

**JSON on stdout:** exit 0/1: the outcome (status, commits_replayed, conflicts_resolved, files_resolved, files_stale, force_pushed, fork_point, squashed_commits); exit 3: ConflictReport (status, files, tree, classes, remaining_commits, pre_rebase_head); exit 4: RefusalReport (branch, signal, detail, status, override, commits_ahead — a count on the git signals, null on pr_merged, which is asked before checkout; pr_number — set on pr_merged only).

| `classes` | Meaning |
|---|---|
| `regenerate` | A lockfile with a known regenerator: Cargo.lock, Gemfile.lock, bun.lock, bun.lockb, composer.lock, go.sum, package-lock.json, pnpm-lock.yaml, poetry.lock, uv.lock, yarn.lock. Without --fix: rebuilt by its regenerator; the rebase continues only when every conflict in the step is regenerate or accept_theirs and every rebuild succeeds; otherwise the run aborts to a clean tree (exit 3, tree clean), or pauses with --stop-on-conflict (exit 3, tree paused). With --fix: rebuilt by its regenerator, deferred to the end of the step. |
| `accept_theirs` | A generated file (a regenerate=/linguist-generated gitattribute or a generated-file header). Without --fix: the incoming side is staged, then rebuilt by its regenerate= attribute or the declared rebase.regenerate commands (left stale in files_stale when there is neither); the rebase continues only when every conflict in the step is regenerate or accept_theirs and every rebuild succeeds; otherwise the run aborts to a clean tree (exit 3, tree clean), or pauses with --stop-on-conflict (exit 3, tree paused). With --fix: as without --fix, and the repo's conventional generate task (for example mise) is also tried. |
| `delete` | A modify/delete conflict: one side deleted the file. Without --fix: the run aborts to a clean tree (exit 3, tree clean), or pauses with --stop-on-conflict (exit 3, tree paused). With --fix: the deletion is accepted (git rm --force). |
| `binary_error` | A binary file. Without --fix: the run aborts to a clean tree (exit 3, tree clean), or pauses with --stop-on-conflict (exit 3, tree paused). With --fix: never resolved automatically: the rebase pauses with the file in files. |
| `ai_merge` | A hand-written text file. Without --fix: the run aborts to a clean tree (exit 3, tree clean), or pauses with --stop-on-conflict (exit 3, tree paused). With --fix: merged by AI region by region; an answer that loses its markers, keeps a conflict marker, or no longer parses (gofmt -e, ast.parse, bash -n, JSON/YAML/TOML) leaves the file unresolved and the rebase paused. |

| `tree` | Meaning |
|---|---|
| `paused` | the rebase is still in the worktree; what was resolved is staged; an auto-stash is held |
| `clean` | the rebase was aborted; HEAD is at pre_rebase_head |

| `signal` | Meaning |
|---|---|
| `pr_merged` | status already_landed: GitHub reports the PR merged |
| `empty_diff` | status already_landed: the branch has commits but no diff against its base — what a squash merge leaves behind |
| `commits_upstream` | status already_landed: every commit already has an upstream equivalent by patch id (same diff, per `git patch-id`) |
| `no_merge_base` | status unrelated_history: the branch and its base share no commit |
| `conflicts_over_budget` | status conflicts_over_budget: conflicts span more files than automatic resolution should attempt; the rebase was aborted |
| `resolutions_over_budget` | status conflicts_over_budget: the same files conflict commit after commit, past the AI call budget; the rebase was aborted |
| `tracker_refused` | status tracker_unread: GitHub was not asked whether the PR merged (budget latch), so landing could not be ruled out |

> `--force` is a deprecated alias of `--force-landed`.

#### pr push contract

Push HEAD to origin under a lease on the remote tip you planned from. Never rebases, never creates a branch, never edits the PR.

**Writes remotely**

| Trigger | Writes | Where | How | If refused |
|---|---|---|---|---|
| `(bare invocation)` | refs/heads/<branch> := HEAD | origin | git push [--no-verify] --force-with-lease=refs/heads/<branch>:<lease>, where <lease> is the tip origin holds now (read with ls-remote) when it is --expect or an ancestor of --expect (a fast-forward); when origin cannot be asked, --expect itself. Records PushDomain in state.json, no rebase. | origin at neither --expect nor an ancestor of it: refused before pushing (stale --expect), exit 1. origin has no such branch: exit 1 — pr push never creates a branch. origin moves between the read and the push: stale info, nothing written, exit 1. |
| `--no-verify` | nothing extra: changes how the push runs | origin | adds --no-verify to git push, skipping the git pre-push hook; the workbench owner check still runs | as for the push |

**Exit codes**

| Code | Meaning | Next |
|---|---|---|
| `0` | pushed, and origin holds HEAD |  |
| `1` | refused, lost, unverified, or a rebase is in progress | `git -C {worktree} ls-remote origin refs/heads/{branch}` → `if HEAD belongs on top of what origin holds, run pr rebase first; otherwise re-run pr push --expect <that SHA>` |
| `2` | --expect missing or empty |  |
| `75` | another pr run holds this branch or checkout; retry once it finishes |  |

**Examples**

- `pr push --expect 0123456789abcdef0123456789abcdef01234567` — push HEAD if origin still holds that commit, or an ancestor of it
- `pr push --expect <sha> --no-verify` — the same, skipping the pre-push hook

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

Step stdout and stderr stay in the run's `logs/`; `run --verbose` and `resume --verbose`
also stream stderr as `step_log` events. `resume` continues an interrupted run; a
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

`pr board --schema-version 1 --checkout DIR [--checkout DIR ...] --phase listing|status|owed [--prs N,N] [--if-changed STAMP] [--offline]` prints the board document (facts, lanes, badges, per-row `presentation` slots, columns as JSON, `reviews_root`). Missing `--schema-version` exits 2. `--prs` only with `--phase status` (else exit 2); `--offline` only with `--phase listing`. Exit 0 whenever a document prints; per-repo failures live in `repos[].error`. `--if-changed` matching the stamp returns `{schema_version, phase, stamp, unchanged: true}`. Python owns every fact, lane, badge, presentation slot and column; no draft CI exemption; clients hide a row whose effective team lane is `landed`. `complete` is false for `status --prs` and `owed` (clients merge by `(repo, number)`).

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

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates), plus a root `AGENTS.md` — the file every harness reads (Claude Code natively from 2.1.277, which otto-workbench's AGENTS.md support assumes; `ai sync` warns on an older one). `AGENTS.md` is the only name `ai init` ever writes, and a `CLAUDE.md` is never created, not even as a pointer: Claude Code reads `AGENTS.md` only when no `CLAUDE.md` exists anywhere in the directory chain, so one at any level hides the sibling `AGENTS.md`. A repo that already has one keeps it untouched — `ai init` writes no `AGENTS.md` beside it (`--force` does, and names the leftover), and the SessionStart hook and `ai sync` suggest folding it into `AGENTS.md` and deleting it. `bin/local/validate-no-claude-md` fails on a tracked `CLAUDE.md`, and `--registered` checks every registered repo. Nothing deletes it for you.
