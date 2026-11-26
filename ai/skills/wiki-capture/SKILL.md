---
name: wiki-capture
description: "Reviews sessions on disk since the last capture for anything worth keeping and logs it to the knowledge base, without writing articles. TRIGGER when: a capture is due in a repo with a knowledge base. SKIP: writing or editing articles (use wiki compile); one-off answers already in the codebase."
source: otto-workbench/ai/skills/wiki-capture/SKILL.md
invocation: "/wiki-capture"
trigger: "Auto-triggers at session end in repos that have a knowledge base, once 24h and 3 sessions have both passed since the last capture. Reviews transcripts on disk for that repo, not the session that spawned the run."
skip: "Never writes or edits an article — /wiki compile processes what this logs, deliberately."
output: "SESSION_OBSERVATION entries appended to the knowledge base's _log.md"
lifecycle_cadence: "24h"
lifecycle_scope: per-project
---

# Wiki Capture

Passive knowledge capture. Transcripts for this repo since the last capture are
reviewed for anything durable, and what turns up is **logged** — never written
into an article.

Capture runs at session end once the repo is both 24 hours and 3 sessions past
its last one — `should-wiki-capture.sh` gates on the two together, so a single
session the next day does not trigger one.

Passive capture is the half of the wiki pattern that makes it compound. Without
it every article enters the knowledge base because a human remembered to run
`/wiki ingest`, and the knowledge produced by ordinary debugging sessions — the
kind most worth keeping — is lost unless somebody thinks to save it.

This runs unattended, which is exactly why it does not compile. An unattended
session cannot judge whether a finding supersedes an article or contradicts one;
`/wiki compile` makes that call deliberately, with a human present, and
`wiki lint` reports what is logged as unprocessed until it does.

## 1. Find the knowledge base

```bash
wiki path
```

Exit 2 means there is none. Say nothing and stop — do not create one.

## 2. Read what happened

A maintenance spawn and a Stop-hook auto-task are new sessions with no work
context. Review the repo's transcripts on disk, not the session you are in.

```bash
bash ~/.agents/skills/wiki-capture/wiki-capture-list.sh
```

One absolute path per line, every harness, every worktree of this repo, newer
than `last-wiki-capture`. The current session is omitted. Empty output is
success — skip to step 5.

Read each path. Human turns live in the records; skip automation preambles.
Globbing `~/.claude/projects` finds only Claude Code's. Read the knowledge
base's `_index.md` too, so an observation can name the article it bears on.

## 3. Decide — conservatively

Log an observation only when the session produced something that:

- **contradicts** an existing article,
- **fills a gap** an article leaves open, or
- **supersedes** what is recorded.

A session that read code and changed nothing produced nothing. A session that
fixed a typo produced nothing. Be strict: these are logged unattended on every
qualifying session, and `wiki lint` counts every one of them until a compile
drains it. An empty capture is the normal outcome and a good one.

Nothing worth logging is not a failure. Skip to step 5.

## 4. Append to `_log.md`

One entry per finding, in the form every session on this machine is told to use:

```
[{DATE}] SESSION_OBSERVATION: {what was learned}
  Context: {what prompted it}
  Articles: [[existing-article]] if any
```

Append only. Never edit an existing entry, never edit an article, never run
`wiki index`, never compile.

## 5. Record completion

```bash
bash ~/.agents/skills/wiki-capture/wiki-capture-complete.sh
```

Run this whether or not anything was logged. The cooldown resets on the review
having happened, not on it having found something — a capture that logged
nothing and did not stamp would re-run on every session exit for the rest of
the day.
