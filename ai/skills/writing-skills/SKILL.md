---
name: writing-skills
description: "Write or change a skill in this workbench — SKILL.md frontmatter, the agent: contract, and the generators to re-run. TRIGGER when: creating a skill, editing a SKILL.md, changing lifecycle hooks, or asked how to write a skill. SKIP: invoking an existing skill; authoring a rule (see rules-authoring)."
source: otto-workbench/ai/skills/writing-skills/SKILL.md
invocation: "/writing-skills"
trigger: "Use when creating a new skill, editing an existing SKILL.md, changing auto-triggered lifecycle behavior, or when the user asks how to write or author a skill."
skip: "Do not use for invoking a skill that already exists, or for authoring a coding rule (see rules-authoring.md)."
---

<!-- Overrides superpowers:writing-skills, 2,589 lines that know nothing about
     ai/skills/, the dual-harness install, or agent: splicing — the three things
     that decide whether a skill written here works at all.

     This file wins by name collision: Pi ranks ~/.agents/skills (scope user,
     origin top-level) ahead of a package resource, so the upstream copy is
     dropped even though the Superpowers extension re-adds its skills directory
     through resources_discover.

     Written against superpowers v6.3.0. When bumping the pin, re-read the
     upstream skill. -->

# Writing Skills

Skills live at `ai/skills/<name>/SKILL.md` and install into both Claude Code
(`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from that one tree. Write it
once; both harnesses read it the same way.

**Announce at start:** "I'm using the writing-skills skill to author this
skill."

## Step 1: Decide It Should Be a Skill

A skill loads when its description matches what the user is doing. A rule in
`ai/guidelines/rules/` is always on. Pick by when the guidance should apply:

| Guidance | Home |
|---|---|
| Applies always, to all work | A rule in `ai/guidelines/rules/` |
| Applies only sometimes, and to both harnesses | A skill |
| Applies only under Claude Code | A rule with `harness: [claude]` |
| A procedure with steps to follow | A skill |

Path-scoped rules (`paths:` frontmatter) reach Claude Code only —
`_pi_rule_reaches_pi` drops them, because Pi loads one context file and cannot
scope. Guidance that must reach both and only sometimes applies is a skill.
That rule is in `rules-authoring.md`, and it is why this skill exists.

## Step 2: Write the Frontmatter

```yaml
---
name: my-skill                      # must equal the directory name
description: "What it does. TRIGGER when: ... SKIP: ..."
source: otto-workbench/ai/skills/my-skill/SKILL.md
invocation: "/my-skill"
trigger: "Use when the user asks to ..."
skip: "Do not use for X (use Y instead)."
---
```

- `name` must equal the directory name, and `source` must be the exact path.
  `bin/local/validate-skills` fails on either mismatch
- `description` is what the model matches against, so it decides whether the
  skill ever fires. Write the `TRIGGER when:` / `SKIP:` clauses in it
- `trigger` and `skip` are full sentences, not comma-separated phrase lists
- `invocation` is the bare `/<name>` form. `/skill:<name>` is upstream
  Superpowers' syntax and resolves nowhere here
- `invocation` is required unless the skill declares `agent:`

**Displacing an upstream skill?** Its `description` has to match both house
vocabulary and the upstream phrasing a user would trigger on. Winning the name
and losing the trigger means the upstream procedure runs anyway.

## Step 3: The `agent:` Contract

`agent: <name>` declares that the skill's body is an agent protocol maintained
in `ai/claude/agents/<name>.md`. Such a skill:

- installs to **Pi's discovery root only** — Claude Code loads the same protocol
  as an agent, and must not carry a second copy as a skill
- carries `<!-- AGENT_PROTOCOL_PLACEHOLDER: -->` where the body goes;
  `ai/skills/steps.sh` splices the agent's body in at install time
- needs no `invocation:`, because there is no command to type

Every agent matched to a *situation* rather than dispatched by code needs such a
stub, or its protocol reaches Claude Code and not Pi. `validate-skills` fails on
an agent file with neither a stub nor an entry in its `PROGRAMMATIC_AGENTS`
list.

## Step 4: Code Blocks Avoid Bash Parameter Expansion

Never use `${var//pattern/replacement}` or `${var#pattern}` in a SKILL.md code
block. Claude Code's static analyzer cannot parse them and triggers a permission
prompt every time:

- `echo "$var" | tr '/' '-'` instead of `${var//\//-}`
- `echo "$var" | sed 's/pattern/replacement/g'` for complex substitutions

This applies to code blocks in SKILL.md files, which run through the Bash tool.
Standalone `.sh` scripts run directly and are unaffected.

Skills are shared between harnesses, so this binds a Pi-authored SKILL.md too:
the permission prompt it causes lands on Claude Code users.

## Step 5: Lifecycle Fields, If Auto-Triggered

When adding or changing auto-triggered behavior — hooks, cooldowns, pending
flags — update **both** the `should-*.sh` script constants and the skill's
`lifecycle_*` frontmatter. They are two halves of one contract, and a skill
whose stated cadence disagrees with its hook is a skill that fires on a schedule
nobody wrote down.

## Step 6: Re-run the Generators

After adding or changing a skill, an agent, or a task:

```bash
bin/local/generate-tool-context
bin/local/compose-docs
```

The first regenerates the AI rule files; the second regenerates the generated
sections of `ai-automation.md`, `tools.md`, and `components.md`. Skipping them
leaves the skill invisible to the harnesses that read those files.

Never edit a `docs/*.md` carrying a "Generated from … by bin/local/compose-docs"
banner — edit its `docs/*.src.md`, or the source data behind the include
directive.

## Step 7: Validate

```bash
bin/local/validate-skills
```

It checks the name/directory match, the exact `source:` path, the required
fields, and the agent-stub pairing. A skill is not done until it passes.

## Quick Reference

| Situation | Action |
|-----------|--------|
| Always-on guidance | A rule, not a skill |
| Sometimes-on, both harnesses | A skill |
| Sometimes-on, Claude only | A rule with `harness: [claude]` |
| Body is an agent protocol | `agent:` + placeholder, no `invocation:` |
| Need a string transform in a code block | Pipe to `tr`/`sed`, never `${var//}` |
| Auto-triggered | `lifecycle_*` **and** the `should-*.sh` constants |
| Finished editing | `generate-tool-context`, `compose-docs`, `validate-skills` |
| Generated doc looks wrong | Edit the `.src.md`, never the output |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "I'll scope the rule with `paths:` instead" | Path-scoped rules never reach Pi. If Pi needs it, it is a skill. |
| "The generators run in CI" | `validate-docs-composed` fails the push. Run them yourself. |
| "`${var//}` is cleaner" | It costs a permission prompt on every invocation, for every Claude Code user. |
| "I'll add `invocation:` to the agent stub too" | There is no command to type; the protocol arrives by dispatch. |
| "The description is close enough" | The description is the trigger. A skill that never fires is not installed, it is just present. |
