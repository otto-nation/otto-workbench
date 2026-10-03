/**
 * Backstop for a Pi session that started at a bare-repo container.
 *
 * See ./detect.ts for what such a session is missing and why. When the session
 * started at a container, this loads the worktree's context files into the
 * system prompt as project instructions, adds a section saying what it could
 * not load and where commands must run, and tells the person at the keyboard.
 * Everywhere else it does nothing.
 *
 * The files are whatever Pi's own `loadProjectContextFiles` returns for the
 * worktree, so this loads exactly what Pi would have loaded had the session
 * started there — no second list of file names to drift from Pi's.
 *
 * Both are set by mutating `systemPromptOptions` rather than returning
 * `systemPrompt`: a returned prompt replaces the whole structured prompt for
 * the run, while mutated options are diffed and recorded as a delta.
 *
 * The answer is computed once per session and cwd: `before_agent_start` fires
 * on every turn, and the container a session started at does not change.
 */

import {
  getAgentDir,
  loadProjectContextFiles,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import {
  containerContext,
  noticeFor,
  sectionFor,
  SECTION_NAME,
  worktreeFiles,
  type ContainerContext,
  type ContextFile,
} from "./detect.ts";

interface Found {
  ctx: ContainerContext;
  files: ContextFile[];
}

function find(cwd: string): Found | null {
  const ctx = containerContext(cwd);
  if (!ctx) return null;
  if (ctx.kind !== "resolved") return { ctx, files: [] };
  const all = loadProjectContextFiles({ cwd: ctx.worktree, agentDir: getAgentDir() });
  return { ctx, files: worktreeFiles(all, ctx.worktree) };
}

export default function (pi: ExtensionAPI) {
  let cached: { cwd: string; found: Found | null } | undefined;

  const foundFor = (cwd: string): Found | null => {
    if (cached?.cwd !== cwd) cached = { cwd, found: find(cwd) };
    return cached.found;
  };

  pi.on("session_start", async (_event, ctx) => {
    cached = undefined;
    const found = foundFor(ctx.cwd);
    if (found && ctx.hasUI) {
      ctx.ui.notify(noticeFor(found.ctx, found.files.map((f) => f.path)), "warning");
    }
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const found = foundFor(ctx.cwd);
    if (!found) return;

    const options = event.systemPromptOptions;
    const have = new Set(options.contextFiles.map((f) => f.path));
    options.contextFiles.push(...found.files.filter((f) => !have.has(f.path)));
    options.sections[SECTION_NAME] = sectionFor(found.ctx, found.files.map((f) => f.path));
  });
}
