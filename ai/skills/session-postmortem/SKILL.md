---
name: session-postmortem
description: "Write a living local session postmortem on this machine. TRIGGER when: the user asks for a local or session postmortem, living notes for this session, or runs /session-postmortem. SKIP: Grafana IRM or production-incident writeups (repo postmortem skill; incident for triage); knowledge-base log lines (wiki-capture)."
source: otto-workbench/ai/skills/session-postmortem/SKILL.md
invocation: "/session-postmortem"
trigger: "Use when the user asks for a local or session postmortem, living notes for this session, or runs /session-postmortem."
skip: "Do not use for Grafana IRM or production-incident writeups — that is the repo postmortem skill, or incident for triage. Do not use for knowledge-base log lines — that is wiki-capture."
output: "~/.local/state/workbench/sessions/<id>/POSTMORTEM.md"
---

# Session postmortem

A living write-up of **this session**, on this machine. Not Grafana. Not a
production incident. The file is local state; it is never committed.

Maximum's `postmortem` skill drafts a Linear issue from a resolved Grafana IRM
incident. This skill does not call it, does not read IRM, and does not mention
Grafana in the file beyond the header line that says this is not that.

## Where the file goes

```bash
mkdir -p ~/.local/state/workbench/sessions/<id>
```

Then write `~/.local/state/workbench/sessions/<id>/POSTMORTEM.md`.

`<id>` is the harness session id: `$PI_SESSION_ID` under Pi, otherwise the
Claude session uuid. If neither is known, ask. Do not invent an id, and do not
use the branch name, the PR number, or a date as a stand-in.

One file per session. If that path already exists, update it in place — do not
start a second file, and do not copy it into a worktree, `workspace/`, or the
repo.

`pr gc` already removes `~/.local/state/workbench/pr/*/POSTMORTEM.md` with the
PR target (merged or closed) and `~/.local/state/workbench/batch/*/POSTMORTEM.md`
with the batch run (7 days, or 30 when the run kept unpublished work). Leave
those files where they are. Do not copy them here. Operator-placed notes under
`workspace/ops` or `workspace/plans` stay there too.

## Living vs final

The same file. Create it as living the first time this skill runs in the
session. Keep updating it. When the session's work is clean, replace the
living sentence in the header with `Final.` Do not write a second copy.

## What to record

```markdown
# Session postmortem — <short title>

Living notes. Local to this machine. Not a Grafana IRM postmortem.

Session: `<id>`
Date: <ISO date>
Worktree: `<path>` (`<branch>`)
Prompt: <what was asked>

## Operator directions

## Work log

## Remaining after this session

## Changes needed after this session

## Findings
```

- **Operator directions** — constraints the user set this session (models,
  `--no-verify`, one PR at a time). Facts, not restated rules.
- **Work log** — what actually happened, with SHAs, PR URLs, and commands that
  mattered. Status of each PR if this session touched any.
- **Remaining** — work this session did not finish. Named, not a backlog dump.
- **Changes needed** — durable follow-ups (a skill, a rule, a product change).
  Each one names what was observed and what should change. Do not implement
  them from this skill, and do not file issues unless the user asked.
- **Findings** — facts that are not follow-ups (a wrong diagnosis, a flag that
  does not mean what a doc said).

A **fact** is something this session did or read. An **inference** starts with
`Inference:`. A gap is `Needs a human:` plus what is missing. Do not fill a gap
with a plausible story.

Do not invent Grafana fields, Linear issues, or customer impact. Do not mention
that an agent wrote the file.

## When to write

The first time the user asks, create the file. Later asks in the same session
update it. "Keep a postmortem" mid-session means write the living file now, not
at exit. A session that produced nothing still gets the header and prompt if
the user asked — an empty capture is `wiki-capture`'s rule, not this one's.
