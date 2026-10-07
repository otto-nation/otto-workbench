import { readFileSync } from "node:fs";
import { dirname } from "node:path";

/**
 * Builds the superpowers bootstrap as a system-prompt section.
 *
 * Upstream's Pi extension (obra/superpowers, .pi/extensions/superpowers.ts)
 * delivers the bootstrap by splicing a user message in front of the transcript
 * from a `context` hook, and stops splicing once the first agent run ends. Every
 * request after that sends a transcript whose first message is gone. Anthropic
 * binds each signed thinking block to the content that preceded it, so the next
 * request carrying an earlier thinking block is rejected with a 400 naming
 * `messages.0`. A system-prompt section is rebuilt identically on every run and
 * never touches the transcript, so nothing a thinking block was signed against
 * moves.
 *
 * ai/pi/settings.json filters upstream's extension out of the package and keeps
 * its skills; this module replaces only the delivery.
 *
 * Imports nothing but node built-ins, so tests/pi_extensions_bootstrap.bats can load it
 * under plain `node` — index.ts is the half that only resolves inside Pi.
 */

/** Section tag the bootstrap renders under. Pi accepts `^[a-z][a-z0-9_-]*$`. */
export const SECTION_NAME = "superpowers_bootstrap";

/** The skill whose body is the bootstrap. */
export const BOOTSTRAP_SKILL = "using-superpowers";

/** The two fields of Pi's `Skill` this module reads. */
export interface SkillRef {
  name: string;
  filePath: string;
}

export type ReadFile = (path: string) => string;

const readUtf8: ReadFile = (path) => readFileSync(path, "utf8");

/**
 * The section body for the loaded skills, or null when there is nothing to add.
 *
 * Keyed off the skill set Pi actually loaded, not a fixed path: a run started
 * with `--no-skills` — the review pipeline's agents, whose answers are parsed
 * rather than read — loads no skills, and so gets no bootstrap telling it to
 * announce one first. An operator override of using-superpowers is picked up the
 * same way, since Pi resolves the name before this sees it.
 */
export function bootstrapSection(
  skills: readonly SkillRef[],
  read: ReadFile = readUtf8,
): string | null {
  const skill = skills.find((s) => s.name === BOOTSTRAP_SKILL);
  if (!skill) return null;

  let content: string;
  try {
    content = read(skill.filePath);
  } catch {
    // A skill Pi listed but cannot be read is a broken install, which Pi's own
    // skill loader already reports. Failing the run here would block every turn
    // over a missing preamble; the session works without it, just unprimed.
    return null;
  }

  const body = stripFrontmatter(content);
  if (!body) return null;

  return `The ${BOOTSTRAP_SKILL} skill is included below and is already loaded for this session. Follow it now; do not load it again.

Pi has no \`Skill\` tool. Where a Superpowers instruction says to invoke a skill, read that skill's SKILL.md with the \`read\` tool when it applies.

Pi ships no standard subagent tool. If one such as \`subagent\` is available, use it for Superpowers subagent workflows; otherwise do the work in this session or explain the missing capability, rather than inventing \`Task\` calls. Where \`references/pi-tools.md\` names \`pi-subagents\`, read it as whichever subagent tool is installed.

Superpowers names model tiers (cheap, standard, most capable), not models. Translate a tier using the subagent tool's own model guidance and pass \`model\` explicitly on every dispatch, including templates that carry no \`model:\` line — unless that guidance says this session pins subagents to the parent model, in which case omit it.

Pi ships no standard task-list tool. If an installed todo/task tool is available, use it; otherwise track work in a plan file or a repo-local \`TODO.md\`. Treat \`TodoWrite\` references as this task-tracking action.

Relative paths in the skill below, such as \`references/pi-tools.md\`, resolve against ${dirname(skill.filePath)}.

${body}`;
}

/** The skill body with its YAML frontmatter removed, trimmed. */
export function stripFrontmatter(content: string): string {
  const match = content.match(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n([\s\S]*))?$/);
  return (match ? (match[1] ?? "") : content).trim();
}
