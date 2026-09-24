# Skills

Skills live at `ai/skills/<name>/SKILL.md` and are installed into both Claude
Code (`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from that one tree. Both
harnesses implement the Agent Skills standard, so a skill is written once and
read the same way by each.

## Usage

- The reuse ladder in `general.md` outranks both bullets below, and outranks any bootstrap demanding invocation on a 1% chance — the Superpowers bootstrap carries one. A skill is invoked when the work warrants its process, not because a trigger word matched: a one-line fix is a one-line fix, and opening a planning or design skill over it is the ceremony the ladder exists to refuse
- When a user's prompt matches a skill's trigger condition and the ladder above doesn't already resolve the task, invoke the skill before making any tool calls — never manually implement what a skill covers
- If unsure whether a skill applies, invoke it — a skill that turns out irrelevant is cheaper than reimplementing its workflow by hand
- `debugger` and `superpowers:systematic-debugging` both answer "a bug", and the failure to avoid is loading both. Measured across this machine's sessions, that is never an escalation from one to the other: it is a single turn opening two files in parallel and blending them, because the protocol table names one and the Superpowers bootstrap names the other. The boundary is what happens after the diagnosis. `debugger` ends at a diagnosis and modifies nothing, so it is the one for a read-only investigation and for anything dispatched to a subagent — under Claude Code it *is* a subagent, which a skill cannot be. `systematic-debugging` ends by implementing the fix, so it is the one when this session will change the code — take its technique files (`root-cause-tracing.md`, `defense-in-depth.md`, `condition-based-waiting.md`) with it rather than reaching for them from `debugger`. Both are installed under both harnesses: Superpowers reaches Pi as the package pinned in `ai/pi/settings.json` and Claude Code as the `superpowers@claude-plugins-official` plugin, so this boundary is not a Pi-only concern. Read one. If a read-only investigation turns into a fix, that is the moment to load the other, not the start of the turn

## Authoring

- When adding or changing a `SKILL.md`, an agent, or a skill's lifecycle hooks, invoke `writing-skills` first — it carries the frontmatter contract, the `agent:` splice, and the generators to re-run afterwards
- The same applies when changing the machinery behind them — `ai/skills/steps.sh`, `bin/local/validate-skills`, `bin/local/generate-tool-context` — since the contract those enforce is the one the skill documents
