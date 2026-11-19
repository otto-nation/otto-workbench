/**
 * The session brief's pure half: when a brief is due, what the model is shown and
 * asked, how its answer is read, whether the session title may change, and the
 * `pi-sessions` command lines. Node built-ins and `_shared/spend.ts` only, so
 * tests/pi_extensions_session_brief.bats loads it under plain `node`; wire.ts
 * holds the handlers.
 */

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import type { SpendItem } from "../_shared/spend.ts";

export { spendFromEntries } from "../_shared/spend.ts";

export const BRIEF_TYPE = "session-brief";
/** T3-a: a brief is written after this long with no new input. */
export const IDLE_MS = 120_000;
export const TRANSCRIPT_CHARS = 12_000;
export const TITLE_CHARS = 60;
export const TLDR_CHARS = 400;
const COMMAND_CHARS = 200;

/** The checkout's ai/bin/pi-sessions: this file is loaded from its real path, not the install symlink. */
export const PI_SESSIONS = fileURLToPath(new URL("../../../bin/pi-sessions", import.meta.url));
/** lib/config_cli.py, the one reader of config.yml — the source model-router reads its tiers from. */
const CONFIG_CLI = fileURLToPath(new URL("../../../../lib/config_cli.py", import.meta.url));

export interface PrRef { repo: string; number: number; url: string; title: string }
export interface Facts { worktrees: string[]; prs: PrRef[] }
export interface Brief { title: string; tldr: string }
export interface BriefData {
  title_set: string | null;
  name: string | null;
  tldr: string;
  worktrees: string[];
  prs: PrRef[];
  at: string;
  spend: SpendItem[];
}
export interface TierRef { provider: string | null; id: string }
interface Entry {
  type?: string;
  customType?: string;
  data?: unknown;
  message?: { role?: string; content?: unknown };
}

/** Interactive sessions only: json/print are subagents and `pr` pipeline runs, rpc is embedded. */
export function shouldBrief(mode: string): boolean {
  return mode === "tui";
}

/** True when an assistant message follows the last brief (T3-a: only if there are new turns). */
export function needsBrief(entries: readonly Entry[]): boolean {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.type === "custom" && e.customType === BRIEF_TYPE) return false;
    if (e.type === "message" && e.message?.role === "assistant") return true;
  }
  return false;
}

export function latestBrief(entries: readonly Entry[]): Partial<BriefData> | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.type === "custom" && e.customType === BRIEF_TYPE) return (e.data ?? {}) as Partial<BriefData>;
  }
  return null;
}

/** T3-f: rename only an unnamed session, or one still carrying the title this extension set. */
export function shouldRename(current: string | null | undefined, titleSet: string | null | undefined): boolean {
  if (!current) return true;
  return titleSet != null && current === titleSet;
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && typeof part === "object" && (part as { type?: unknown }).type === "text")
    .map((part) => String((part as { text?: unknown }).text ?? ""))
    .join("\n");
}

function linesOf(entry: Entry): string[] {
  const message = entry.type === "message" ? entry.message : undefined;
  if (!message) return [];
  if (message.role === "user") {
    const said = textOf(message.content).trim();
    return said ? [`User: ${said}`] : [];
  }
  if (message.role !== "assistant" || !Array.isArray(message.content)) return [];
  const out: string[] = [];
  const said = textOf(message.content).trim();
  if (said) out.push(`Assistant: ${said}`);
  for (const part of message.content as Array<{ type?: unknown; name?: unknown; arguments?: { command?: unknown } }>) {
    if (part?.type === "toolCall" && part.name === "bash" && typeof part.arguments?.command === "string") {
      out.push(`$ ${part.arguments.command.slice(0, COMMAND_CHARS)}`);
    }
  }
  return out;
}

/** The recent turns as text — user and assistant text, bash commands — newest kept within MAXCHARS. */
export function transcript(entries: readonly Entry[], maxChars = TRANSCRIPT_CHARS): string {
  const lines = entries.flatMap(linesOf);
  const kept: string[] = [];
  let size = 0;
  for (let i = lines.length - 1; i >= 0; i--) {
    const cost = lines[i].length + (kept.length ? 1 : 0);
    if (size + cost > maxChars) {
      // A single newest line longer than the cap still says what the session is on.
      if (!kept.length) kept.unshift(lines[i].slice(-maxChars));
      break;
    }
    kept.unshift(lines[i]);
    size += cost;
  }
  return kept.join("\n");
}

export const SYSTEM_PROMPT = [
  "You name and summarise a coding-agent session from its recent turns.",
  'Reply with one JSON object and nothing else: {"title": "...", "tldr": "..."}.',
  `title: what the session is working on, at most ${TITLE_CHARS} characters, no trailing period.`,
  "tldr: one to three sentences on what it is or was doing and where it stands.",
  "Do not list PR numbers or file paths; those are recorded separately.",
].join("\n");

export function briefContext(text: string, nowMs: number) {
  return {
    systemPrompt: SYSTEM_PROMPT,
    messages: [{ role: "user" as const, content: [{ type: "text" as const, text: `Recent turns:\n\n${text}` }], timestamp: nowMs }],
  };
}

export function replyText(content: unknown): string {
  return textOf(content);
}

export function parseBrief(text: string): Brief | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  let value: { title?: unknown; tldr?: unknown };
  try {
    value = JSON.parse(text.slice(start, end + 1));
  } catch {
    return null;
  }
  const title = typeof value?.title === "string"
    ? value.title.trim().replace(/\.+$/, "").slice(0, TITLE_CHARS).trim() : "";
  const tldr = typeof value?.tldr === "string" ? value.tldr.trim().slice(0, TLDR_CHARS) : "";
  return title && tldr ? { title, tldr } : null;
}

/** The light tier from `config models` output TEXT; null when it is missing or unreadable. */
export function lightTierRef(text: string | null): TierRef | null {
  if (!text) return null;
  try {
    const light = JSON.parse(text)?.tiers?.light;
    if (!light || typeof light.id !== "string" || !light.id) return null;
    return { provider: typeof light.provider === "string" && light.provider ? light.provider : null, id: light.id };
  } catch {
    return null;
  }
}

/**
 * `config models` output, or null when it cannot run. Read per brief, not cached:
 * a brief is minutes apart, and a tier changed in config.yml reaches the next one.
 */
export function readTiersText(): string | null {
  try {
    return execFileSync("python3", [CONFIG_CLI, "models"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      env: { ...process.env, NO_COLOR: "1" },
      timeout: 10_000,
    });
  } catch {
    return null;
  }
}

interface ModelLike { provider: string; id: string }
interface RegistryLike<M extends ModelLike> {
  find(provider: string, id: string): M | undefined;
  getAvailable(): M[];
}

/**
 * The model for REF: by provider and id when the tier names a provider; otherwise
 * the available model with that id, on the session model's provider first — a
 * tier written as a bare id (`claude-haiku-4-5@…`) names no provider.
 */
export function pickModel<M extends ModelLike>(
  registry: RegistryLike<M>, ref: TierRef, current: { provider: string } | undefined,
): M | undefined {
  if (ref.provider) return registry.find(ref.provider, ref.id);
  const matches = registry.getAvailable().filter((model) => model.id === ref.id);
  return matches.find((model) => model.provider === current?.provider) ?? matches[0];
}

export function factsArgv(file: string): string[] {
  return [PI_SESSIONS, "facts", file, "--json"];
}

export function liveArgv(pid: number, file: string): string[] {
  return [PI_SESSIONS, "live", "--pid", String(pid), "--session-file", file];
}

export function clearArgv(pid: number): string[] {
  return [PI_SESSIONS, "live", "--clear", "--pid", String(pid)];
}

export const EMPTY_FACTS: Facts = Object.freeze({ worktrees: [], prs: [] }) as Facts;

export function parseFacts(stdout: string): Facts {
  try {
    const value = JSON.parse(stdout);
    const worktrees = Array.isArray(value?.worktrees)
      ? value.worktrees.filter((w: unknown) => typeof w === "string") : [];
    const prs = Array.isArray(value?.prs)
      ? value.prs.filter((p: { repo?: unknown; number?: unknown }) =>
        p && typeof p.repo === "string" && Number.isInteger(p.number)) : [];
    return { worktrees, prs };
  } catch {
    return { worktrees: [], prs: [] };
  }
}

export function briefData(fields: {
  titleSet: string | null; name: string | null; tldr: string; facts: Facts; at: Date;
  spend?: SpendItem[];
}): BriefData {
  return {
    title_set: fields.titleSet,
    name: fields.name,
    tldr: fields.tldr,
    worktrees: fields.facts.worktrees,
    prs: fields.facts.prs,
    at: fields.at.toISOString(),
    spend: fields.spend ?? [],
  };
}
