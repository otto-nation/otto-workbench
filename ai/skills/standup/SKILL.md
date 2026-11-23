---
name: standup
description: "Inventory the current user's PRs in this GitHub org and format a standup update (landed, waiting on review, approved, blocked, drafts). TRIGGER when: user asks for a standup, standup update, or what landed vs what they are working on. SKIP: reviewing a single PR (use code-review or pr-comments); sweeping many PRs with pr batch; filing or pushing."
source: otto-workbench/ai/skills/standup/SKILL.md
invocation: "/standup"
trigger: "Use when the user asks for a standup, standup update, or a landed vs working-on breakdown of their PRs."
skip: "Do not use for reviewing one PR, addressing review comments, or running pr batch."
---

# Standup

Chat-only inventory of **your** PRs in **one GitHub org**. Default org is the
owner of the repo this session is in. If the user names a different org, pass
`--org` to the gatherer.

Do not ping anyone. Do not open, close, or edit PRs.

## Cheap path

The facts come from the gatherer (no model). Formatting is a cheap read-only
pass over that JSON. Do **not** call `gh` yourself, and do not walk the git
tree.

1. Run the gatherer from the session repo (add `--org ORG` only when the user
   named one):

```bash
standup
```

A non-zero exit is a hard stop: print stderr and do not invent buckets.

2. Format the JSON. Dispatch a read-only `gemini` subagent at `thinking: low`
   when the harness has one; otherwise format it yourself from the JSON and
   stop. The formatter sees only the JSON. It does not run `gh`.

## Output

Use these sections, in this order. Omit a section that has no rows.

## Landed

Grouped by theme when there are enough rows (docs, CI/tooling, product, …).

## Waiting on review

`waiting_on_review` — not draft, mergeable, CI green, not approved.

## Approved, not merged

`approved_not_merged`

## CI / conflicts (mine)

`ci_or_conflicts` — append the fail names or `conflicts` after the diff.

## Still draft (not ready)

`still_draft`

Each row is one line:

`url — title — +N/−M`

Use the JSON `url`, `title`, `additions`, `deletions`. If `deletions` is null,
write `+N` only. No indented body. No file counts. Full GitHub URLs.

Do not list drafts under Waiting on review. Do not list approved PRs there
either.
