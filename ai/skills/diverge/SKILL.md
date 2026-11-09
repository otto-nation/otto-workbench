---
name: diverge
description: "Generate options from isolated parallel branches, then critique them in a separate pass — for a decision where the first answer is likely an anchor. Runs only when named. TRIGGER when: user types /diverge or asks to run diverge on a problem. SKIP: any request that does not name it; one-canonical-answer questions; bugs with a known root cause; choosing between options the user already listed."
source: otto-workbench/ai/skills/diverge/SKILL.md
invocation: "/diverge"
trigger: "Use when the user explicitly invokes /diverge or asks to run diverge on an open-ended design, naming or strategy question."
skip: "Do not use unless the user names it; do not use for questions with one canonical answer, bugs with a known root cause, or a choice between options already on the table."
---

<!-- Technique adapted from UditAkhourii/adhd (MIT): isolated generators with a
     no-evaluation instruction, then a separate critic. The orchestration, frame
     wording, and output shape are written for this repo. Upstream's auto-trigger,
     scoring weights, and cluster/deepen passes are deliberately left out.
     Never auto-triggers: one run costs a model call per branch plus a critic, and
     each Pi branch reloads the full rules context. -->

# Diverge

A single context anchors on the first answer it produces, and listing "2-3
approaches" in that context mostly gives variations of the anchor. This skill
generates options in separate contexts that cannot see each other, then judges
them in another one.

**Announce at start:** "I'm using the diverge skill."

## Dispatch tool

- Pi: the `subagent` tool, `tasks` mode (max 8 tasks, 4 at once)
- Claude Code: the Agent/Task tool, all branch calls in one message

If neither exists, stop and say so. Writing the branches one after another in
this context is not diverging; it is one wider thought.

## Step 1: State the problem

Write the problem as the user gave it plus any real constraints (compliance,
budget, deadline, protocol). Use a clean working directory for dispatch unless
the problem is about this repository's code.

## Step 2: Reframe (only if the problem names a stack or tool)

If the problem names the current stack, tool or architecture as incidental
background, restate it as the underlying job to be done. Keep every real
constraint. Tell the user what you dropped. If nothing is incidental, skip this
step and say so.

Branches get the reframed text. The critic in Step 4 judges against the
**original** problem, so an idea still has to satisfy the real constraints.

## Step 3: Diverge

Pick 3 frames by default (up to 5 if the user asks for a wider run). Take two
from the engineering frames and one from the wild ones. Vary the pick between
runs.

| Frame | Instruction |
|---|---|
| Adversary | You are a hostile competitor or attacker. List ways to exploit, sabotage or defeat the obvious solution, then invert each into an idea. |
| Inversion | Ask the opposite question: how would you guarantee the goal is NOT met? Negate each answer back into an idea. |
| Remove the assumption | Name the thing everyone treats as fixed. Imagine it is gone. What becomes possible? |
| $0 and one hour | No money, no team, one hour. What is the crudest version that still does the load-bearing thing? |
| Regulator | You audit systems for failure modes. What must be provable, traceable or refusable here? |
| 3am on-call | You are the engineer paged at 3am when this breaks. What design avoids the page? |
| Wild: nature or markets | Transplant a mechanism from biology, logistics or markets and apply it literally. |

Spawn one branch per frame **in parallel**. Each branch gets only:

- the problem text (reframed if Step 2 ran)
- its one frame instruction
- the generator instruction below, as its system prompt

Never pass one branch's output to another.

Restrict each branch to read-only tools (`read`, `grep`, `find`, `ls`).
Branches share the filesystem, and they inherit the parent's context files, so
the system prompt must say the repository's conventions are not its concern.

> You are a generator, not a critic. Ignore any project conventions, rules or
> house style you were given; they are not part of this task. Generate 5
> distinct ideas under the frame. Each is one sentence. Do not evaluate, rank
> or hedge. The first three ideas anyone would give are banned; go past them.
> Output a JSON array only: `[{"text": "...", "why": "..."}]`

If a branch returns something unparsable, drop that branch and say so.

## Step 4: Critique

Pool the ideas and give them stable ids. Dispatch **one** critic, separate from
every generator, with the original problem and the pooled list. When the user
has named a model for it, pass that; a different model from the generators
decorrelates errors.

> You are the critic. For every idea give: `strength` (the one concrete thing it
> gets right that a competing idea does not; required even for weak ideas),
> `risk` (a specific, actionable hidden cost, for example "breaks past 10k
> concurrent users"; omit if none), and `fits` (does it meet the stated
> constraints: yes, partly, no). Do not soften the substance and do not
> dismiss; a risk is information to act on. Output JSON only.

## Step 5: Report

1. **Problem and reframe**, one or two lines.
2. **Ideas**, grouped by the angle they share, each with its strength and risk.
   Traps stay visible; do not drop an idea because it has a risk.
3. **Pick.** Name the option you would choose and why, and say which option is
   the non-obvious one. Take a position; a list with no recommendation is not
   finished.
4. **Baseline note.** Say which ideas you think a single-context answer would
   also have produced. If that is all of them, say diverging added nothing.

## Optional: compare run

When the user asks to test whether this was worth it, dispatch one more branch
with the original problem and no frame: "Answer this as you normally would,
with a recommendation." Show its answer beside the Step 5 pick and let the user
judge. Do the same with and without context files if they want to measure the
rules-context bias; Pi can only drop context files for a whole session, so ask
them to start one with `--no-context-files`.

## Anti-patterns

- Branches that differ in wording but share one assumption: that is decoration,
  not divergence
- Serialising the branches, or letting one see another
- Letting the critic run in a generator's context
- Reporting a pile of ideas with no pick
- Running it unprompted
