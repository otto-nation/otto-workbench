import { spawnSync } from "node:child_process";
import { sep } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * What a Pi session started at a bare-repo container is missing, kept apart
 * from the extension.
 *
 * Pi loads context files from its starting directory and that directory's
 * parents, never from the directories below it. A bare-repo container holds
 * the bare .git and the worktrees as peers, so a session started there loads
 * the user's own AGENTS.md and nothing of the repository's — and nothing says
 * so: a session that read no repo instructions looks exactly like one that read
 * them all. The zsh `pi()` wrapper (zsh/config.d/tools/_worktree_launch.zsh)
 * keeps most sessions from starting there; this is the layer behind it, for
 * `command pi`, launchers that are not zsh, and subagents spawned at a
 * container.
 *
 * Which context files the worktree contributes is Pi's own answer:
 * index.ts asks the SDK's `loadProjectContextFiles` for the worktree, so the
 * candidate names, their order, overrides and BOM handling cannot drift from
 * what Pi would have loaded had the session started there. This file only
 * decides whether there is a container and what to tell the session.
 *
 * It imports node built-ins only, so tests/pi_container_context.bats can run it
 * under plain `node`; index.ts is the wiring that only resolves inside Pi.
 */

/** Where `resolve-worktree` lives, resolved from this file rather than $PATH. */
export const RESOLVE_WORKTREE = fileURLToPath(
  new URL("../../../../bin/resolve-worktree", import.meta.url),
);

/** Section tag the notice renders under. Pi accepts `^[a-z][a-z0-9_-]*$`. */
export const SECTION_NAME = "container_context";

/** `resolve-worktree`'s exit codes, from bin/resolve-worktree. */
const RESOLVED = 0;
const UNRESOLVED = 1;

/** A container whose worktree was found. */
export interface Resolved {
  kind: "resolved";
  container: string;
  worktree: string;
}

/** A container whose worktree could not be found, and why. */
export interface Unresolved {
  kind: "unresolved";
  container: string;
  reason: string;
}

export type ContainerContext = Resolved | Unresolved;

/**
 * GIT_DIR and friends stripped. Git skips discovery entirely when GIT_DIR is
 * set, so one inherited from a hook would make every answer below about the
 * hook's repository — a wrong answer arriving as a success.
 */
function gitEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;
  return env;
}

/** Whether DIR is a bare repository, asked of git directly. */
function isBare(dir: string): boolean {
  const run = spawnSync("git", ["-C", dir, "rev-parse", "--is-bare-repository"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
    env: gitEnv(),
  });
  return run.status === 0 && run.stdout.trim() === "true";
}

/**
 * What CWD is missing because the session started there, or null when it is
 * not a bare-repo container — the ordinary case, which costs one spawn.
 *
 * The worktree is `resolve-worktree`'s answer and nobody else's: the zsh
 * wrapper and Claude Code's SessionStart hook ask the same script, so no layer
 * can name a different worktree from the others.
 *
 * A resolver that cannot run is not taken to mean "not a container". That
 * would switch this layer off at exactly the moment the wrapper in front of it
 * is also off, since it asks the same script. Git answers whether CWD is bare,
 * and a bare CWD with no resolver is reported as unresolved.
 */
export function containerContext(
  cwd: string,
  resolver: string = RESOLVE_WORKTREE,
): ContainerContext | null {
  const run = spawnSync(resolver, [cwd], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: gitEnv(),
  });

  if (run.error) {
    if (!isBare(cwd)) return null;
    return {
      kind: "unresolved",
      container: cwd,
      reason: `resolve-worktree could not run (${resolver}: ${run.error.message})`,
    };
  }

  if (run.status === RESOLVED) {
    const worktree = run.stdout.trim();
    // An empty path is no answer: `worktreeFiles` would build the prefix "/"
    // from it and take every absolute path for a file inside the worktree.
    if (!worktree) {
      return {
        kind: "unresolved",
        container: cwd,
        reason: "resolve-worktree exited 0 but printed no worktree path",
      };
    }
    return { kind: "resolved", container: cwd, worktree };
  }

  if (run.status === UNRESOLVED) {
    const said = run.stderr.trim();
    return {
      kind: "unresolved",
      container: cwd,
      reason: said || "resolve-worktree found no worktree for the default branch",
    };
  }

  // 2 is "not a bare repository"; 64 is a usage error, which cannot arise for a
  // directory that exists. Neither is a container this layer can speak for.
  return null;
}

/** A context file as Pi's loader returns it. */
export interface ContextFile {
  path: string;
  content: string;
}

/**
 * The files from FILES — Pi's loader run for WORKTREE — that live inside
 * WORKTREE. Pi's loader also returns the user's own AGENTS.md and anything in
 * the worktree's ancestors; the session already has those, because the
 * container sits under the same ancestors. Matched on a path separator, so a
 * sibling `main-old/` is not taken for `main/`.
 */
export function worktreeFiles(
  files: readonly ContextFile[],
  worktree: string,
): ContextFile[] {
  const prefix = worktree.endsWith(sep) ? worktree : worktree + sep;
  return files.filter((f) => f.path.startsWith(prefix));
}

/**
 * The system-prompt section for CTX. LOADED lists the context files the
 * extension added from the worktree — chosen by Pi's own loader, not here.
 */
export function sectionFor(ctx: ContainerContext, loaded: readonly string[] = []): string {
  const cannot =
    "Pi loaded nothing from the repository's `.pi/` directory — its settings, " +
    "extensions, skills and prompts load only from the directory a session " +
    "starts in, before any extension runs — and every tool call runs here, " +
    "where `git status` fails because a bare repository has no working tree.";

  if (ctx.kind === "unresolved") {
    return (
      `This session started at ${ctx.container}, a bare-repo container, and ` +
      `no worktree could be resolved for it: ${ctx.reason}. None of the ` +
      `repository's context files were loaded. ${cannot} Tell the user, and ` +
      "suggest starting Pi inside one of the repository's worktrees."
    );
  }

  const files = loaded.length
    ? `Its context files have been loaded below as project instructions: ${loaded.join(", ")}.`
    : "That worktree has no context file (AGENTS.md, CLAUDE.md or an override), so none was loaded.";

  return (
    `This session started at ${ctx.container}, a bare-repo container. The ` +
    `worktree that speaks for it is ${ctx.worktree}. ${files} ${cannot} ` +
    `Run git and repository commands in ${ctx.worktree} (\`git -C\`, or \`cd\` ` +
    "within the command), and read project paths relative to it. To load " +
    "everything, the user can restart with `pi` from the container (the shell " +
    "wrapper launches in the worktree) or start Pi inside the worktree."
  );
}

/** The one-line notice shown to the person at the keyboard. */
export function noticeFor(ctx: ContainerContext, loaded: readonly string[] = []): string {
  if (ctx.kind === "unresolved") {
    return `pi: started at bare container ${ctx.container} with no resolvable worktree — repository context not loaded (${ctx.reason})`;
  }
  return `pi: started at bare container ${ctx.container} — loaded ${loaded.length ? loaded.join(", ") : "no context file"}; repo .pi/ resources not loaded. Relaunch with \`pi\` or start inside ${ctx.worktree}.`;
}
