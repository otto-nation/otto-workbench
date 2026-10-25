/**
 * Which tier a `workbench/auto` request goes to, kept apart from the extension.
 *
 * Imports nothing, so a test loads it under plain `node`: index.ts is loaded
 * only inside a Pi session, and a routing rule that quietly stopped matching
 * would read exactly like one with nothing to do.
 *
 * A session moves through three phases, each on one tier:
 *
 *   standard  — where it starts: ordinary questions and small changes
 *   planning  — deep, from the request after a planning skill loads
 *   building  — standard again, from the request after the first edit outside
 *               the workspace's plans and specs, and for the rest of the session
 *
 * The phase only moves forward, and is router state, so it follows the session
 * tree and survives compaction. Within a phase a tool follow-up stays on the
 * model that answered and a retry on the model that failed, which keeps the
 * prompt cache and thinking signatures valid; a phase change accepts one cache
 * miss. Requests outside the agent loop — compaction summaries, an extension's
 * own model call — go to light and carry no state.
 */

/** Where the virtual model is listed. agent/harness_models.py spells the same two. */
export const ROUTER_PROVIDER = "workbench";
export const ROUTER_ID = "auto";

/** Skills whose load means the session is designing, not yet building. */
export const PLANNING_SKILLS: readonly string[] = ["brainstorming", "writing-plans"];

/** Tools whose successful result means a file was changed. */
const EDIT_TOOLS = new Set(["edit", "write"]);

export type Tier = "deep" | "standard" | "light";
export type Phase = "standard" | "planning" | "building";
export type RouteReason = "user" | "continuation" | "retry" | "direct";

export interface RouterState {
  phase: Phase;
}

const PHASE_TIER: Record<Phase, Tier> = {
  standard: "standard",
  planning: "deep",
  building: "standard",
};

/** The parts of a Pi message this module reads. */
interface Block {
  type?: string;
  text?: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
}

export interface RouteMessage {
  role: string;
  content?: string | readonly Block[];
  toolCallId?: string;
  isError?: boolean;
}

function blocks(message: RouteMessage): readonly Block[] {
  return typeof message.content === "string" ? [{ type: "text", text: message.content }] : (message.content ?? []);
}

function pathArgument(block: Block): string {
  const path = block.arguments?.path;
  return typeof path === "string" ? path : "";
}

/** Whether PATH is the SKILL.md of a planning skill, on any install root. */
function isPlanningSkillFile(path: string): boolean {
  const parts = path.split("/");
  return parts.at(-1) === "SKILL.md" && PLANNING_SKILLS.includes(parts.at(-2) ?? "");
}

/**
 * Whether a planning skill has been loaded: expanded from `/skill:name`, which
 * Pi sends as a user message opening `<skill name="…"`, or read by the model,
 * which is a `read` of its SKILL.md.
 */
export function loadsPlanningSkill(messages: readonly RouteMessage[]): boolean {
  return messages.some((message) =>
    blocks(message).some((block) => {
      if (message.role === "user" && block.type === "text") {
        const name = /<skill name="([^"]+)"/.exec(block.text ?? "")?.[1];
        return name !== undefined && PLANNING_SKILLS.includes(name);
      }
      return block.type === "toolCall" && block.name === "read" && isPlanningSkillFile(pathArgument(block));
    }),
  );
}

/**
 * Whether an edit or write succeeded on a file that is not a plan or a spec.
 * The path is on the tool call and the outcome on its result, so the two are
 * joined by call id; a call with no successful result changed nothing.
 */
export function editsOutsidePlans(
  messages: readonly RouteMessage[],
  isPlanFile: (path: string) => boolean,
): boolean {
  const edits = new Map<string, string>();
  for (const message of messages) {
    for (const block of blocks(message)) {
      if (block.type === "toolCall" && block.id && EDIT_TOOLS.has(block.name ?? "")) {
        edits.set(block.id, pathArgument(block));
      }
    }
  }
  return messages.some((message) => {
    if (message.role !== "toolResult" || message.isError || !message.toolCallId) return false;
    const path = edits.get(message.toolCallId);
    return path !== undefined && !isPlanFile(path);
  });
}

/** The phase after reading MESSAGES. Never moves backward. */
export function nextPhase(
  phase: Phase,
  messages: readonly RouteMessage[],
  isPlanFile: (path: string) => boolean,
): Phase {
  // Only a shortcut: building has no way back below, since planning is entered
  // from standard alone. It spares a building session the scan on every request.
  if (phase === "building") return phase;
  if (editsOutsidePlans(messages, isPlanFile)) return "building";
  if (phase === "standard" && loadsPlanningSkill(messages)) return "planning";
  return phase;
}

export interface Decision {
  /** The tier to route to, unless `keep` says to reuse a model already answering. */
  tier: Tier;
  /** Reuse the model behind `previous` or `failed` instead of the tier's. */
  keep?: "previous" | "failed";
  /** New router state, when the phase changed; undefined keeps the current one. */
  state?: RouterState;
}

export interface RouteInput {
  reason: RouteReason;
  state?: RouterState;
  messages: readonly RouteMessage[];
  hasPrevious: boolean;
  hasFailed: boolean;
  isPlanFile: (path: string) => boolean;
}

/** Where one request goes. */
export function decide(input: RouteInput): Decision {
  if (input.reason === "direct") return { tier: "light" };
  const current = input.state?.phase ?? "standard";
  const phase = nextPhase(current, input.messages, input.isPlanFile);
  const tier = PHASE_TIER[phase];
  if (phase !== current) return { tier, state: { phase } };
  if (input.reason === "retry" && input.hasFailed) return { tier, keep: "failed" };
  if (input.reason === "continuation" && input.hasPrevious) return { tier, keep: "previous" };
  return { tier };
}
