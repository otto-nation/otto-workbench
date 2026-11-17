# Pi extensions

Every subdirectory here except a `_`-prefixed one is installed into
`~/.pi/agent/extensions/` by `otto-workbench ai sync`, and removed from there
when it leaves this tree.
Pi discovers that directory on its own and follows symlinks into it, so an
extension is symlinked rather than copied and loads straight from the checkout.

An extension is a directory holding an `index.ts`:

```
ai/pi/extensions/<name>/index.ts
```

`index.js` and a `package.json` naming its own entry points also work — that is
Pi's rule, not the workbench's. TypeScript needs no build step; Pi loads it
through jiti. A directory with none of the three is skipped with a warning,
because an extension that cannot load is nearly always a mistake worth naming.

```ts
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => { /* … */ });
}
```

Do not start background resources from the factory — defer to `session_start`
and register an idempotent `session_shutdown` handler, or a `/reload` leaks one
per reload.

## What does not go here

An extension only some runs should load belongs in `../extensions-cli/`, which
this step does not install. Anything in *this* directory loads in **every** Pi
session on the machine, including ones with no repository and no environment set
up for it — `review-guard.ts` lives next door for exactly that reason.

Code shared *between* the extensions here goes in a `_`-prefixed directory, as
`_shared/` does. Those are skipped by name and never installed, so the missing
entry point is not reported as a fault every sync — and a shared module that
later grows a `package.json` for its own dependencies still does not start
loading as an extension. The extensions import it by relative path
(`../_shared/statements.ts`) — but only from a module their entry point imports,
never from `index.ts` itself. Pi loads the entry point by its installed symlink
path, so a `../_shared` import written in `index.ts` resolves beside the symlink
in `~/.pi/agent/extensions/`, where nothing is installed, and the extension fails
to load in every new session. A module `index.ts` imports is loaded from its real
path in the checkout, where `../_shared` resolves. So shared code reaches an
entry point through its sibling `detect.ts`, which re-exports what `index.ts`
needs, and only the extension directories have to reach
`~/.pi/agent/extensions/`. `tests/pi_extensions_install.bats` fails on an entry
point that imports a shared module directly.

`wait-guidance.ts` is the refusal alternative sleep-guard and background-guard
share: `job_start` when `getActiveTools()` lists it *and* the session is not
json/print, otherwise a foreground bash call bounded by the tool's timeout.
A json/print process exits at the end of the turn, so naming `job_start` there
— even when the tool is listed, as it is in a default subagent — points at work
that cannot outlive the process.

`github-writes.ts` answers whether a command writes to GitHub: an in-process
superset pre-filter, then `ai/bin/owners-check --classify` for the verdict, so
the Python classifier stays the only copy of the write shapes. Anything but exit 0
with exactly `read` on stdout — missing, crashed (an uncaught exception exits
1), timed out, unexpected output — counts as a write. The checker is the
checkout's `ai/bin/owners-check`, or an `owners-check` on PATH when the
extensions run from somewhere without one. `runOwnersCheck` is the one runner
and timeout every owners-check call goes through.

upstream-guard is the owner boundary, in every session mode: for a command the
pre-filter matches it runs `owners-check --cwd`, which allows reads and decides
whether a write's target is one of our owners (`github.owners`) and whether its
published text names an owner in a different group. A refusal, or a check that
could not run, blocks the command. The user's overrides for publishing anyway
are never honoured here; an agent command that names `WORKBENCH_ALLOW_UPSTREAM`
or `WORKBENCH_ALLOW_CROSS_REF` is refused.

The same check covers the Pi tools that publish without bash
(`gh_pr_post_comment`, `gh_pr_reply_comment`, `gh_pr_bulk_reply`): `tool-calls.ts`
writes each body to a temporary file and asks `owners-check` about the
equivalent `gh api` call run from the session's directory, so every remote there
must be in one group and every body must stay inside it. The tool names are a
list in `tool-calls.ts`; a new publishing tool upstream is unguarded until listed.
The Linear write tools' text fields (`LINEAR_TEXT_TOOLS`, including a release `version`) go to `owners-check --text`,
judged against the group of the session's checkout. oneshot-publish-guard refuses
both sets in one-shot sessions.

## agent-worktree

Publishes the worktree an interactive session works in on its herdr terminal
(`hc label $HC_TERMINAL_ID otto.worktree=<path>`), so Open session's adopt-pane
in `_lib/run_session.js` can take an agent pane in the checkout, and claims that
reached worktree for the session. `tool_result` for bash, edit and write sends a
Claude-shaped payload (`detect.ts` `payloadFor`) to `ai/bin/report-agent-worktree`
unawaited; `session_start` sends `--clear`. Every `tui` session (`shouldReport`);
herdr is only required for the label — the hook skips labelling without one.
Subagents and `pr` pipeline sessions (`json`/`print`/`rpc`) neither claim nor
label. The hook env carries the session pid only when `claimsWorktree` is true.
The hook is the one writer shared with Claude Code's `PostToolUse`/`SessionStart`
entries in `ai/claude/settings.json`; which worktree a call targeted is
`ai/lib/pr/agent_worktree.py`'s answer, and `tests/pi_extensions_agent_worktree.bats`
feeds both payload shapes through it. A feature, not a guard: its Claude Code twin
is those settings entries.

## run-session

The pi session of one `pr batch` run (`pr batch session RUN_ID`,
`ai/lib/batch/run_session.py`) or of one PR target (`pr target session`,
`ai/lib/batch/target_session.py`). It does nothing unless the session already
holds a `workbench-run` entry (so `/resume` and `pi --session` keep working; the
entry's `kind` says which, and one without a `kind` is a batch run) or was
started as an own session: the kind's directory variable set *and* its session
id (`batch-<run-id>`; for a target, `WORKBENCH_SESSION_ID`). The variable alone
is not enough — every pi the session dispatches through `pr … --fix` inherits
it. The kind table in `_shared/run-dir.ts` is the one predicate and the one
place the two differ (variables, ids, `pr` argv, wording); a test fails when a
literal naming either kind appears elsewhere in `run-session/` or `session-lock/`.

This is a deliberate exception to the `extensions-cli/` rule in AGENTS.md: it
loads in every Pi session because `pr batch session` starts pi with no
`--extension`, and `/resume` and `pi --session` carry none either. Do not move it
to `extensions-cli/`; it stays inert unless the entry or env and session id match.

- **History.** `<run-dir>/events.jsonl`, written only by Python, is filed as
  `workbench-run-history` entries: a per-PR timeline, one line per step, the
  last 12 lines per PR per block with older ones folded into a count. Custom
  entries never reach the model; the system note points it at the file.
- **Now.** `pr batch report RUN_ID --offline --format json`, each finding with
  its latest `decision`, sent as a `workbench-run-now` custom message — custom
  messages are model context. It is rebuilt before every turn and re-sent only
  when it changed. Opening a session never starts a turn.
- **Cursor.** How far the history has been shown (a byte offset) and Now's
  digest are filed as `workbench-run` entries; the latest on the branch wins.
- **`record_decision`** runs `pr batch note` (a target: `pr target note`), the only writer of a `decision`.
  A decision changes no item state.
- **System note.** The `workbench_run` prompt section says what the session is
  for and that nothing is pushed unless the operator says so.

`events.ts` and `now.ts` import nothing, `session.ts` only the kind table; `detect.ts` adds node
built-ins; `wire.ts` holds the handlers with SDK types only, so
`tests/pi_extensions_run_session.bats` drives them with a stand-in `pi`.
`index.ts` only adds pi-tui's `Text`. It is a feature, not a guard, so it has
no Claude Code twin.

session-lock records no claim in a run's or target's own session
(`claimsWorktree` in `_shared/run-dir.ts`): its cwd is the run's checkout or the
target's worktree, and a claim there would make `--fix` steps refuse the tree
they edit. agent-worktree uses the same predicate before putting the session on
the hook env. A target session's system note forbids direct file edits, since
the exemption removes the guard against a fix pass committing over them.

## Overriding one

`~/.config/workbench/overrides/ai/pi/extensions/<name>/` replaces the extension
of that name, and `<name>.disabled` beside it suppresses the shipped one. Same
layering as `ai/skills`.
