# Skills

Skills live at `ai/skills/<name>/SKILL.md` and are installed into both Claude
Code (`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from that one tree. Both
harnesses implement the Agent Skills standard, so a skill is written once and
read the same way by each.

## Usage

- The reuse ladder in `general.md` outranks both bullets below, and outranks any bootstrap telling you to invoke on a 1% chance — such as Superpowers' guidance to invoke a skill whenever there is even a small chance it applies. A skill is invoked when the work warrants its process, not because a trigger word matched: a one-line fix is a one-line fix, and opening a planning or design skill over it is the ceremony the ladder exists to refuse
- When a user's prompt matches a skill's trigger condition and the ladder above doesn't already resolve the task, invoke the skill before making any tool calls — never manually implement what a skill covers
- If unsure whether a skill applies, invoke it — a skill that turns out irrelevant is cheaper than reimplementing its workflow by hand

## Authoring

- When adding or changing a `SKILL.md`, an agent, or a skill's lifecycle hooks, invoke `writing-skills` first — it carries the frontmatter contract, the `agent:` splice, and the generators to re-run afterwards
- The same applies when changing the machinery behind them — `ai/skills/steps.sh`, `bin/local/validate-skills`, `bin/local/generate-tool-context` — since the contract those enforce is the one the skill documents
