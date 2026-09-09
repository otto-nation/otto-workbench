---
name: wiki-capture
description: "Reviews the session that just ended for anything worth keeping and logs it to the knowledge base, without writing articles. TRIGGER when: a session ends in a repo with a knowledge base and a capture is due. SKIP: writing or editing articles (use wiki compile); one-off answers already in the codebase."
source: otto-workbench/ai/skills/wiki-capture/SKILL.md
invocation: "/wiki-capture"
trigger: "Auto-triggers at session end, at most once every 24h per repo, in repos that have a knowledge base."
skip: "Never writes or edits an article — /wiki compile processes what this logs, deliberately."
output: "SESSION_OBSERVATION entries appended to the knowledge base's _log.md"
lifecycle_cadence: "24h"
lifecycle_scope: per-project
---

# Wiki Capture

Passive knowledge capture. The session that just ended is reviewed for anything
durable, and what turns up is **logged** — never written into an article.

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

Read the most recent transcript for this repo under
`~/.claude/projects/<slug>/*.jsonl`, and read the knowledge base's `_index.md`
so an observation can name the article it bears on.

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
bash ~/.claude/skills/wiki-capture/wiki-capture-complete.sh
```

Run this whether or not anything was logged. The cooldown resets on the review
having happened, not on it having found something — a capture that logged
nothing and did not stamp would re-run on every session exit for the rest of
the day.
