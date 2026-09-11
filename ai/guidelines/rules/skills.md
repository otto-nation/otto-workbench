# Skills

Skills live at `ai/skills/<name>/SKILL.md` and are installed into both Claude
Code (`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from that one tree. Both
harnesses implement the Agent Skills standard, so a skill is written once and
read the same way by each.

## Usage

- When a user's prompt matches a skill's trigger condition, invoke the skill before making any tool calls — never manually implement what a skill covers
- If unsure whether a skill applies, invoke it — a skill that turns out irrelevant is cheaper than reimplementing its workflow by hand

## Authoring

- When adding or changing a `SKILL.md`, an agent, or a skill's lifecycle hooks, invoke `writing-skills` first — it carries the frontmatter contract, the `agent:` splice, and the generators to re-run afterwards
