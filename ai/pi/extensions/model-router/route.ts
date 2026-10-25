/**
 * Which tier a `workbench/auto` request goes to, kept apart from the extension.
 *
 * Imports nothing, so a test loads it under plain `node`: index.ts is loaded
 * only inside a Pi session, and a routing rule that quietly stopped matching
 * would read exactly like one with nothing to do.
 *
 * A session is in one of three phases, each on one tier:
 *
 *   standard  — where it starts: ordinary questions and small changes
 *   planning  — deep, while the latest of the two events below is a planning
 *               skill loading
 *   building  — standard, while the latest is a successful edit outside the
 *               workspace's plans and specs
 *
 * So writing the plan keeps a session planning, the first code edit after it
 * moves it to building, and loading a planning skill again — a second feature
 * in one session — brings deep back. The phase is router state: it follows the
 * session tree, and when compaction has removed both events from view the
 * stored phase stands. Within a phase a tool follow-up stays on the model that
 * answered and a retry on the model that failed, which keeps the prompt cache
 * and thinking signatures valid; a phase change accepts one cache miss.
 * Requests outside the agent loop — compaction summaries, an extension's own
 * model call — go to light and carry no state, unless light cannot hold the
 * conversation (index.ts).
 *
 * ceiling: an edit is an `edit` or `write` tool call, so a build done entirely
 * through `bash` (sed -i, git apply, a generator) stays on deep. Upgrade to
 * counting bash writes — `_shared/statements.ts` already tokenizes commands —
 * if sessions are seen implementing that way while still planning.
 */

/** Where the virtual model is listed. agent/harness_models.py spells the same two. */
export const ROUTER_PROVIDER = "workbench";
export const ROUTER_ID = "auto";

/** Skills whose load means the session is designing, not building. */
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

function loadsPlanningSkill(message: RouteMessage): boolean {
  return blocks(message).some((block) => {
    if (message.role === "user" && block.type === "text") {
      const name = /<skill name="([^"]+)"/.exec(block.text ?? "")?.[1];
      return name !== undefined && PLANNING_SKILLS.includes(name);
    }
    return block.type === "toolCall" && block.name === "read" && isPlanningSkillFile(pathArgument(block));
  });
}

/**
 * Where a planning skill last loaded, -1 for nowhere: expanded from
 * `/skill:name`, which Pi sends as a user message opening `<skill name="…"`, or
 * read by the model, which is a `read` of its SKILL.md.
 */
export function lastPlanningSkillAt(messages: readonly RouteMessage[]): number {
  return messages.findLastIndex(loadsPlanningSkill);
}

/**
 * Where an edit or write last succeeded on a file that is not a plan or a
 * spec, -1 for nowhere. The path is on the tool call and the outcome on its
 * result, so the two are joined by call id; a call with no successful result
 * changed nothing.
 */
export function lastEditOutsidePlansAt(
  messages: readonly RouteMessage[],
  isPlanFile: (path: string) => boolean,
): number {
  const edits = new Map<string, string>();
  for (const message of messages) {
    for (const block of blocks(message)) {
      if (block.type === "toolCall" && block.id && EDIT_TOOLS.has(block.name ?? "")) {
        edits.set(block.id, pathArgument(block));
      }
    }
  }
  return messages.findLastIndex((message) => {
    if (message.role !== "toolResult" || message.isError || !message.toolCallId) return false;
    const path = edits.get(message.toolCallId);
    return path !== undefined && !isPlanFile(path);
  });
}

/**
 * The phase MESSAGES put the session in: whichever of a planning skill and a
 * code edit came last, else PHASE when neither is in view.
 *
 * ceiling: scans every message on every request. It is an in-memory pass over
 * objects Pi already holds, and plan verdicts are memoised per path
 * (detect.ts). Upgrade to scanning from an offset kept in RouterState if a
 * routed request is ever measured spending noticeable time here.
 */
export function nextPhase(
  phase: Phase,
  messages: readonly RouteMessage[],
  isPlanFile: (path: string) => boolean,
): Phase {
  const skill = lastPlanningSkillAt(messages);
  const edit = lastEditOutsidePlansAt(messages, isPlanFile);
  if (skill > edit) return "planning";
  if (edit > skill) return "building";
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
